# ClipTV sample clips

Sample sounds that ship with ClipTV so there is something to play out of the
box. Format: WAV, 16-bit PCM, mono, 22050 Hz. Each clip has ~45 ms of leading
silence so the codec's start-of-playback transient falls before the sound.

## Licences and sources

| File | Sound | Licence | Source / author |
|------|-------|---------|-----------------|
| `phone_ring_90s.wav` | 1990s electronic telephone ring | **CC0** | Synthesised for ClipTV (`tools/synth_clips.py`) |
| `dog_bark.wav` | Dog barking | **CC BY-SA 4.0** | "Dog barking" by Dr. Nono YesMaybe, Wikimedia Commons — https://commons.wikimedia.org/wiki/File:Dog_barking.webm |
| `pig_grunt.wav` | Pig grunting/oinking | **Public domain** | "oinks" by stilgar (pdsounds.org), via Wikimedia Commons — https://commons.wikimedia.org/wiki/File:Man_grunts_like_pig.ogg |
| `cat_meow.wav` | Cat meowing | **CC0** | "Meow of a Siamese cat" by freemaster2 (Freesound), via Wikimedia Commons — https://commons.wikimedia.org/wiki/File:Meow_of_a_Siamese_cat_-_freemaster2.wav |

The animal clips were trimmed, resampled to 22050 Hz mono and normalised from
the sources above. `dog_bark.wav` is a derivative of a CC BY-SA 4.0 work and is
itself distributed under CC BY-SA 4.0; attribution is given above. The other
clips are CC0 / public domain (attribution appreciated but not required).
