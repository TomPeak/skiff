#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""Build the main-loop code for size (build.py): the functions defined in SIZE_FILES get LLVM's
minsize (about -Oz) while everything else (the sound, render and ISR code, the flash and OTA code,
the main loop) keeps -Os. The JieLi clang 4 has no '#pragma clang attribute' and its minsize
attribute is an error on variables, so build.py compiles felucca.c to LLVM IR without the
optimizer, this script adds 'minsize' to those definitions, and the IR is then compiled at -Os
(without the edit, that round trip is byte-identical to compiling felucca.c directly). (Skiff 2.5) Of those, a
helper that only one of GIANTS calls also gets 'noinline' (giant_helpers).
  size_fns.py IN.ll OUT.ll"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SRC = _ROOT / "firmware" / "src" if (_ROOT / "firmware" / "src").is_dir() else _ROOT / "src"   # either layout
# the UI, the stores, the editor and the console: main loop only, never called by sound code; and (1.1) every
# other file without DSP: USB, MIDI, the sequencer, the drawing, the LCD, OTA, ... Of those, what an interrupt
# reaches (ISR_ROOTS and everything they call or name, from the IR) keeps -Os (isr_reach)
SIZE_FILES = ["ui.c", "favorites.c", "menu_items.c", "icons.c", "ui_graph.c", "ui_draw.c", "ui_menu.c", "ui_input.c",
              "storage.c", "upreset.c", "project.c", "settings_persist.c",
              "editor.c", "editor_preferences.c", "editor_backup.c", "editor_menu.c", "console.c",
              "usb.c", "midi_uart.c", "midi_control.c", "midi_clock.c", "seq.c", "chord.c", "motion.c",
              "panel.c", "lcd.c", "gfx.c", "ota.c", "ota_hw.c", "up_fm6.c", "slice_store.c", "params.c", "editor_fm6.c",
              "ui_layer.c", "ui_name.c", "ui_slice.c", "ui_events.c", "main.c", "libc.c", "storage_hw.c", "engines.c", "fm4_convert.c",
              # (Skiff) its own main-loop files: the cards, the page graphs, the themes, FM6 LAB, DRUM-X's pages, HOME,
              # TAKES, SEQ TOOLS, the pool and its editor commands (what an interrupt reaches of them stays -Os)
              "ui_blocks.c", "ui_pgraph.c", "ui_crypt.c", "ui_neon.c", "ui_quark.c", "ui_fm6.c", "ui_dx.c", "ui_home.c",
              "ui_take.c", "ui_tools.c", "bank.c", "take.c", "editor_project.c", "editor_song.c", "midi_learn.c", "launch.c", "perform.c", "follow.c", "throw.c", "master.c", "sync_out.c", "cast.c",
              # (Tom Peak) the sound's files too: what neither interrupt reaches (isr_reach follows ENGINES[] and the
              # other tables), the FM6 patches' pack and load, SLICE's manual edits, the sample scans, the lane
              # names (2026-11-28: 700 B less; every render, note-on and voice path stays -Os)
              "audio.c", "click.c", "drum_voice.c", "drumx_voice.c", "dsp.c", "duck.c", "eng_analog.c", "eng_digital.c",
              "eng_drum.c", "eng_drumx.c", "eng_fm6.c", "eng_formant.c", "eng_grain.c", "eng_lofi.c", "eng_noise.c",
              "eng_phase.c", "eng_phys.c", "eng_sample.c", "eng_slice.c", "eng_trio.c", "eng_wheel.c", "fm6_core.c",
              "fx.c", "mod.c", "phys_dsp.c", "phys_symp.c", "slicer.c", "voice.c"]
ISR_ROOTS = ("fm1_alnk0_irq", "fm1_timer5_irq")      # hal/fm1_isr.S: the audio ISR, the key / LED scan (+ USB poll)
DEF = re.compile(r"^(?:static|void|int|uint\w*|int\w*|const)\b[^;=(]*?\b([A-Za-z_]\w*)\s*\(", re.M)
IR_DEF = re.compile(r"^(define [^\n]*?@\"?([\w.]+)\"?\([^\n]*\)(?: unnamed_addr| local_unnamed_addr)?)( #\d+[^\n]*\{)$",
                    re.M)


def definitions(text):
    """names of the functions text defines (a signature at column 0 whose ')' is followed by '{')"""
    names = []
    text = re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.S)        # comments (none hold code)
    for m in DEF.finditer(text):
        i, depth = m.end() - 1, 0
        while i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
            if depth == 0:
                break
        rest = re.sub(r"^(__attribute__\s*\(\(.*?\)\)\s*)+", "", text[i:i + 200].lstrip())
        if rest.startswith("{"):
            names.append(m.group(1))
    return names


def size_names():
    names = set()
    for f in SIZE_FILES:
        names.update(definitions((SRC / f).read_text()))
    return names


IR_BODY = re.compile(r"^define [^\n]*?@\"?([\w.]+)\"?\((.*?)^\}", re.M | re.S)
IR_REF = re.compile(r"@\"?([\w.]+)\"?")
IR_GLOBAL = re.compile(r"^@\"?([\w.]+)\"? = ([^\n]*)$", re.M)


def isr_reach(ir):
    """the functions an interrupt can run: ISR_ROOTS and, transitively, every function their bodies call or
    name (a pointer taken there counts as called), from the unoptimised IR (before inlining: a superset).
    (Tom Peak) A global they name counts too, with every function its initializer names: the engines' render
    and note-on in ENGINES[] are how the audio ISR reaches them"""
    body = {m.group(1): m.group(2) for m in IR_BODY.finditer(ir)}
    glob = {m.group(1): m.group(2) for m in IR_GLOBAL.finditer(ir)}
    seen, todo = set(), [r for r in ISR_ROOTS if r in body]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        text = body.get(f, glob.get(f, ""))
        todo += [g for g in IR_REF.findall(text) if (g in body or g in glob) and g not in seen]
    return {f for f in seen if f in body}


# (Skiff 2.5) the giant main-loop functions: a helper that only one of them calls, inlined there, adds its locals to
# that frame (ed_handle's 1,948 B, draw_graph's 1,680 B, ui_input's 1,624 B: past the short stack offsets) and costs
# more than the call it saves. Out of line, all of them: ~3 KB less (the FM-1 dev image, 2026-11-08: 121 helpers
# measured one by one and together; KEEP_INLINE the ones that are smaller inlined; fm1_main's were larger out of line).
# (Tom Peak) draw_layer's and layer_keys' too: 416 B less (2026-11-28, for the scenes' lane mutes; draw_columns' and
# draw_column's were larger); then every main-loop function of 450 B and more measured alone, the 12 smaller with
# theirs out of line kept: 348 B less together; midi_pc_poll's out of line since (16 B less)
GIANTS = ("draw_graph", "ui_input", "ed_handle", "ui_draw_page", "fm_draw", "edit_param", "draw_layer", "layer_keys",
          "draw_menu", "text_run", "take_commit", "undo_step", "draw_foot", "graph_tracks", "seq_entry", "slc_user_scan",
          "song_knob", "cursor_keys", "fm_cards_of", "cv_alpha_hc")
KEEP_INLINE = ("unit_g_poll", "graph_follow", "scr_input", "rec_clear_poll")
IR_CALL = re.compile(r"\bcall [^@\n]*@\"?([\w.]+)\"?\(")


def giant_helpers(ir):
    """the functions called once in all, from one of GIANTS (but KEEP_INLINE)"""
    body = {m.group(1): m.group(2) for m in IR_BODY.finditer(ir)}
    calls = {}
    for f in IR_CALL.findall(ir):
        calls[f] = calls.get(f, 0) + 1
    return {f for g in GIANTS for f in IR_CALL.findall(body.get(g, "")) if calls[f] == 1 and f not in KEEP_INLINE}


def main(src, dst):
    ir = Path(src).read_text()
    isr = isr_reach(ir)
    names, hit = size_names() - isr, set()
    out = giant_helpers(ir) & names

    def mark(m):
        if m.group(2) not in names:
            return m.group(0)
        hit.add(m.group(2))
        return m.group(1) + " minsize" + (" noinline" if m.group(2) in out else "") + m.group(3)
    Path(dst).write_text(IR_DEF.sub(mark, ir))
    print(f"size: {len(hit)} main-loop functions built for size ({len(names) - len(hit)} not in this build; "
          f"{len(size_names() & isr)} reached from an interrupt kept -Os; {len(out & hit)} of the giants' helpers "
          f"out of line)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
