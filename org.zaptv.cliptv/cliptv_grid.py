# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ClipTV. ClipTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

import lvgl as lv

from mpos import DisplayMetrics, add_focus_border, back_screen

import cliptv_common

UNASSIGNED_COLOR = 0x555555


def add_floating_back_button(screen):
    """Add a Lightning-Piggy-style floating circular back button.

    Bottom-left, floats above the content and stays put while the page
    scrolls. Returns the button.
    """
    button = lv.button(screen)
    button.add_flag(lv.obj.FLAG.FLOATING)
    size = max(32, DisplayMetrics.pct_of_height(15))
    button.set_size(size, size)
    button.set_style_radius(lv.RADIUS_CIRCLE, lv.PART.MAIN)
    button.set_style_bg_opa(lv.OPA._80, lv.PART.MAIN)
    button.align(lv.ALIGN.BOTTOM_LEFT, 4, -4)
    label = lv.label(button)
    label.set_text(lv.SYMBOL.LEFT)
    label.center()
    button.add_event_cb(lambda e: back_screen(), lv.EVENT.CLICKED, None)
    add_focus_border(button)
    return button


def default_button_text(config):
    """Fallback grid text for a button without a custom name."""
    clip = config.get("clip")
    if clip:
        return clip.rsplit("/", 1)[-1]
    playlist = config.get("playlist")
    if playlist:
        symbol = lv.SYMBOL.SHUFFLE if config.get("shuffle") else lv.SYMBOL.LIST
        return "%d %s" % (len(playlist), symbol)
    action = config.get("action")
    if action:
        kind = cliptv_common.ACTION_KINDS.get(action, "any")
        prefix = {"audio": "Audio ", "video": "Video "}.get(kind, "")
        return prefix + lv.SYMBOL.SHUFFLE
    return ""


def add_infinity_badge(parent, color, align=lv.ALIGN.TOP_RIGHT, x_ofs=-3, y_ofs=3,
                       small=False):
    """Add the app's infinity icon, recolored to match the given text color.

    An image is used because no font on the device has the U+267E/U+221E
    infinity glyphs (built-in Montserrat covers ASCII plus FontAwesome
    symbols only). small=True picks the variant sized to sit next to
    14px text, matching the FontAwesome glyphs.
    """
    badge = lv.image(parent)
    icon = cliptv_common.INFINITY_ICON_SMALL if small else cliptv_common.INFINITY_ICON
    badge.set_src(cliptv_common.app_asset(icon))
    if isinstance(color, int):
        color = lv.color_hex(color)
    badge.set_style_image_recolor(color, lv.PART.MAIN)
    badge.set_style_image_recolor_opa(lv.OPA.COVER, lv.PART.MAIN)
    badge.align(align, x_ofs, y_ofs)
    return badge


EQ_BARS = 7


def add_equalizer(button, color):
    """Add a hidden row of equaliser bars along the bottom of a grid button.

    Returns (container, bars). The app shows the container and animates the
    bar heights while the button's audio clip is playing. Bars take the
    label's text color so they contrast on every button color.
    """
    eq = lv.obj(button)
    eq.set_size(lv.pct(72), lv.pct(24))
    eq.align(lv.ALIGN.BOTTOM_MID, 0, -3)
    eq.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
    eq.set_style_border_width(0, lv.PART.MAIN)
    eq.set_style_pad_all(0, lv.PART.MAIN)
    eq.set_style_pad_gap(2, lv.PART.MAIN)
    eq.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    eq.remove_flag(lv.obj.FLAG.SCROLLABLE)
    eq.remove_flag(lv.obj.FLAG.CLICKABLE)
    eq.set_flex_flow(lv.FLEX_FLOW.ROW)
    eq.set_flex_align(lv.FLEX_ALIGN.SPACE_EVENLY, lv.FLEX_ALIGN.END, lv.FLEX_ALIGN.END)
    if isinstance(color, int):
        color = lv.color_hex(color)
    bars = []
    for _ in range(EQ_BARS):
        bar = lv.obj(eq)
        bar.set_size(lv.pct(9), lv.pct(40))
        bar.set_style_bg_color(color, lv.PART.MAIN)
        bar.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        bar.set_style_border_width(0, lv.PART.MAIN)
        bar.set_style_radius(1, lv.PART.MAIN)
        bar.set_style_pad_all(0, lv.PART.MAIN)
        bar.remove_flag(lv.obj.FLAG.SCROLLABLE)
        bar.remove_flag(lv.obj.FLAG.CLICKABLE)
        bars.append(bar)
    eq.add_flag(lv.obj.FLAG.HIDDEN)
    return eq, bars


def style_button(button, label, config):
    """Apply the configured color and name to a grid button."""
    config = config or {}
    if cliptv_common.button_is_assigned(config):
        color = cliptv_common.color_int(config.get("color"))
        text = config.get("name") or default_button_text(config)
    else:
        color = UNASSIGNED_COLOR
        text = lv.SYMBOL.PLUS
    button.set_style_bg_color(lv.color_hex(color), lv.PART.MAIN)
    text_color = 0xFFFFFF if cliptv_common.is_dark_color(color) else 0x000000
    label.set_style_text_color(lv.color_hex(text_color), lv.PART.MAIN)
    label.set_text(text)
    if config.get("loop"):
        add_infinity_badge(button, text_color)


def populate_grid(container, prefs, screen_index, clicked_callback, long_pressed_callback=None):
    """Fill a container with the soundboard button grid for one screen.

    Returns a list of (button, label) tuples indexed by button number.
    """
    container.clean()
    buttons_per_screen = cliptv_common.get_buttons_per_screen(prefs)
    columns, rows = cliptv_common.grid_dimensions(buttons_per_screen)

    container.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
    container.set_style_pad_all(2, lv.PART.MAIN)
    container.set_style_pad_gap(4, lv.PART.MAIN)
    container.set_style_border_width(0, lv.PART.MAIN)
    container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

    width_pct = (100 // columns) - 2
    height_pct = (100 // rows) - 2

    widgets = []
    for button_index in range(buttons_per_screen):
        config = cliptv_common.get_button_config(prefs, screen_index, button_index)
        button = lv.button(container)
        button.set_size(lv.pct(width_pct), lv.pct(height_pct))
        label = lv.label(button)
        label.set_width(lv.pct(100))
        label.set_long_mode(lv.label.LONG_MODE.WRAP)
        label.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
        label.center()
        style_button(button, label, config)
        add_focus_border(button)
        button.add_event_cb(
            lambda e, i=button_index: clicked_callback(screen_index, i),
            lv.EVENT.SHORT_CLICKED,
            None,
        )
        if long_pressed_callback:
            button.add_event_cb(
                lambda e, i=button_index: long_pressed_callback(screen_index, i),
                lv.EVENT.LONG_PRESSED,
                None,
            )
        widgets.append((button, label))
    return widgets
