import numpy as np, struct, sys, os

SR = 22050
OUT = sys.argv[1] if len(sys.argv) > 1 else '.'
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(1990)

def t(dur): return np.arange(int(dur*SR))/SR

def env(sig, a=0.006, r=0.02):
    n=len(sig); e=np.ones(n)
    na=int(a*SR); nr=int(r*SR)
    if na>0: e[:na]=np.linspace(0,1,na)
    if nr>0: e[-nr:]=np.linspace(1,0,nr)
    return sig*e

def biquad_bandpass(x, f0, Q):
    # RBJ bandpass (constant skirt gain), single section
    w0=2*np.pi*f0/SR; alpha=np.sin(w0)/(2*Q); c=np.cos(w0)
    b0=alpha; b1=0.0; b2=-alpha
    a0=1+alpha; a1=-2*c; a2=1-alpha
    b0,b1,b2,a1,a2 = b0/a0,b1/a0,b2/a0,a1/a0,a2/a0
    y=np.zeros_like(x); x1=x2=y1=y2=0.0
    for i in range(len(x)):
        xi=x[i]; yi=b0*xi+b1*x1+b2*x2-a1*y1-a2*y2
        x2,x1=x1,xi; y2,y1=y1,yi; y[i]=yi
    return y

def onepole_lp(x, fc):
    a=np.exp(-2*np.pi*fc/SR); y=np.zeros_like(x); p=0.0
    for i in range(len(x)):
        p=(1-a)*x[i]+a*p; y[i]=p
    return y

def norm(sig, peak=0.89):
    m=np.max(np.abs(sig)) or 1.0
    return sig/m*peak

def save(name, sig):
    sig=norm(env(sig))
    pcm=np.clip(sig,-1,1)
    data=(pcm*32767).astype('<i2').tobytes()
    n=len(data)
    hdr=b'RIFF'+struct.pack('<I',36+n)+b'WAVEfmt '+struct.pack('<IHHIIHH',16,1,1,SR,SR*2,2,16)+b'data'+struct.pack('<I',n)
    open(os.path.join(OUT,name),'wb').write(hdr+data)
    print('%-22s %5.2fs  %6d B' % (name, len(sig)/SR, len(hdr)+n))

# ---------------- 1990s telephone ring ----------------
# electronic warble: fast alternation between two tones, tinny (odd harmonics),
# in the classic cadence — two "brrring" bursts.
def phone():
    def warble(dur):
        tt=t(dur)
        # switch between 1150 and 1400 Hz at ~24 Hz
        sw=(np.sign(np.sin(2*np.pi*24*tt))+1)/2
        f=np.where(sw>0.5,1400,1150)
        ph=2*np.pi*np.cumsum(f)/SR
        tone=np.sin(ph)+0.34*np.sin(3*ph)+0.14*np.sin(5*ph)  # square-ish, tinny
        # per-burst amplitude wobble
        amp=0.9+0.1*np.sin(2*np.pi*48*tt)
        return env(tone*amp, a=0.008, r=0.03)
    gap=np.zeros(int(0.18*SR))
    ring=np.concatenate([warble(0.9), gap, warble(0.9)])
    tail=np.zeros(int(0.12*SR))
    return np.concatenate([ring, tail])

# ---------------- dog bark ----------------
# glottal pulse train, fundamental drops fast; formants ~400/1100/2400; 2 barks
def bark_one(dur=0.22, f_start=520, f_end=250):
    tt=t(dur)
    f=f_start*np.exp(np.log(f_end/f_start)*(tt/dur))
    ph=2*np.pi*np.cumsum(f)/SR
    # buzzy source: sum of harmonics + noise
    src=np.zeros_like(tt)
    for h,amp in ((1,1.0),(2,0.7),(3,0.5),(4,0.32),(5,0.2),(6,0.12)):
        src+=amp*np.sin(h*ph)
    src+=0.25*rng.standard_normal(len(tt))
    body=(1.0*biquad_bandpass(src,430,2.2)
          +0.8*biquad_bandpass(src,1150,3.0)
          +0.5*biquad_bandpass(src,2500,3.5))
    # sharp attack, quick decay
    e=np.exp(-tt*14)*(1-np.exp(-tt*400))
    return body*e

def dog():
    b1=bark_one(0.22,540,255)
    b2=bark_one(0.26,470,220)
    gap=np.zeros(int(0.14*SR))
    return np.concatenate([b1, gap, b2, np.zeros(int(0.08*SR))])

# ---------------- pig grunt ----------------
# low buzzy source, strong amplitude bursts (~9 Hz), snuffle noise, low-pass
def grunt_one(dur, f0):
    tt=t(dur); ph=2*np.pi*f0*tt
    src=np.sin(ph)+0.6*np.sin(2*ph)+0.35*np.sin(3*ph)+0.2*np.sin(4*ph)
    src+=0.5*rng.standard_normal(len(tt))          # snuffle
    src=onepole_lp(src, 900)
    e=np.exp(-tt*11)*(1-np.exp(-tt*260))
    return src*e

def pig():
    parts=[]
    for f0,d,g in ((104,0.16,0.10),(96,0.14,0.11),(112,0.15,0.10),(90,0.20,0.0)):
        parts.append(grunt_one(d,f0)); parts.append(np.zeros(int(g*SR)))
    return np.concatenate(parts)

# ---------------- cat meow ----------------
# pitched source with arcing F0; formants glide  "mmm-ee-ow"; nasal->open->close
def cat():
    dur=0.85; tt=t(dur); x=tt/dur
    f0=280+430*np.sin(np.pi*np.clip(x*1.05,0,1))    # arc up then down
    ph=2*np.pi*np.cumsum(f0)/SR
    src=np.zeros_like(tt)
    for h,amp in ((1,1.0),(2,0.8),(3,0.6),(4,0.45),(5,0.32),(6,0.22),(7,0.14)):
        src+=amp*np.sin(h*ph)
    # formants glide ee(~450/2200) -> a(~800/1200) -> o(~500/900)
    def glide(a,b,c): return np.interp(x,[0,0.45,1.0],[a,b,c])
    f1=glide(360,850,520); f2=glide(2100,1300,950)
    # apply time-varying formants by blending three fixed-band renders
    y=np.zeros_like(src)
    for cf,q,w in ((np.mean(f1[:len(f1)//2]),6,0.0),):
        pass
    # simpler: two static-ish bands at segment means, crossfaded
    seg=len(tt)//3
    out=np.zeros_like(src)
    centers=[(400,2150),(820,1300),(520,950)]
    for i,(a,b) in enumerate(centers):
        s=i*seg; e2=(i+1)*seg if i<2 else len(tt)
        chunk=src[s:e2]
        f=0.9*biquad_bandpass(chunk,a,5)+0.7*biquad_bandpass(chunk,b,6)+0.15*chunk
        out[s:e2]=f
    # amplitude: soft nasal start, open middle, close
    amp=np.interp(x,[0,0.12,0.5,0.8,1.0],[0.2,0.9,1.0,0.8,0.0])
    return out*amp

save('phone_ring_90s.wav', phone())
save('dog_bark.wav', dog())
save('pig_grunt.wav', pig())
save('cat_meow.wav', cat())
