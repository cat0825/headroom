# Menu bar icon prompts

Prompts for generating headroom's menu bar / tray icon. Generate at 1024×1024,
save as PNG, and drop the file at one of:

1. `skills/headroom/assets/menubar-icon.png` — ships with the repo
2. `~/.headroom/icon.png` — personal override
3. `$HEADROOM_ICON` — explicit path

headroom letterboxes any aspect ratio into a square and scales it down, so a
1024×1024 source is plenty. If the file is missing or unreadable, the built-in
drawn icon is used instead — a broken image can never take the tray down.

## The constraint that decides everything

A macOS menu bar is **22 points tall**, and the icon occupies roughly **18×18
points** — about 36×36 physical pixels. Anything thinner than 1/18 of the canvas
disappears; gradients turn to mud; two-pixel details vanish entirely. Every
prompt below is written for that size. Judge a candidate by shrinking it to
36×36 and looking at it from arm's length.

## Shared specification (paste this into every prompt)

```text
A macOS menu bar icon. Square 1024x1024 canvas, subject centred, roughly 10%
padding on all sides. Transparent background, PNG with alpha, no backdrop
rectangle, no rounded-square plate. Flat vector style, solid fills only — no
gradients, no drop shadows, no bevels, no texture, no glow. Minimum stroke
weight 1/18 of the canvas. High contrast, readable when scaled down to 18x18
points. No text, no letters, no numbers, no watermark. Clean geometric shapes,
bold silhouette, crisp edges.
```

## Variant A — gauge ring with a brain (recommended)

Matches the project's own metaphor: a budget meter around a brain.

```text
A circular progress gauge drawn as a bold ring, about 80% of the canvas,
opening at the top. Inside the ring, a simple solid brain glyph in side profile
made from four or five rounded lobes — the kind of brain icon that stays legible
at 18 pixels. The ring is dark slate blue (#14213d); the top-left quarter of the
ring is bright blue (#2563eb) to read as "partly used". The brain is white.

<shared specification>
```

**Why it works:** the ring reads as a meter at any size, and the brain is a
single recognisable blob rather than fine anatomy.

## Variant B — H monogram in a gauge ring

The closest match to the current drawn icon; safest option.

```text
A bold capital letter H, geometric and heavy, centred inside a circular ring.
The ring is dark slate blue (#14213d) and one quarter of it is a brighter accent
blue (#2563eb), like a gauge that is partly filled. The H is white, drawn with
thick uniform strokes and generous counters. The ring and the H share one
optical centre.

<shared specification>
```

## Variant C — battery cell with a brain

A more literal "budget" reading.

```text
A horizontal battery icon, rounded corners, outline only, with a small nub on
the right end. Inside the battery, a brain glyph in side profile fills the
left two thirds. The battery outline is dark slate blue (#14213d), the filled
portion is bright blue (#2563eb), the brain is white. Horizontal composition,
wider than tall, but still centred on a square canvas.

<shared specification>
```

## Negative prompt

```text
photorealistic, 3D render, glossy, metallic, gradient mesh, drop shadow, glow,
outer glow, bevel, emboss, texture, noise, grain, halftone, watermark, signature,
text, letters, numbers, words, multiple icons, collage, grid, border, frame,
rounded-square app tile, background rectangle, white background, busy detail,
thin hairlines, fine hatching, small dots, tiny gaps, two-tone dithering
```

## Checks before you commit an image

1. **Shrink it.** Scale to 36×36 and look at it. If you cannot tell what it is,
   start over.
2. **Both themes.** Put it on a light grey and on a near-black strip. headroom
   draws a dark disc behind the built-in icon precisely so it survives both; a
   custom image has to carry its own contrast.
3. **Alpha.** Open it on a checkerboard — the background must be transparent,
   not white. A white square shows as a visible tile in the menu bar.
4. **No text.** A wordmark is unreadable at 18pt and the menu bar already has
   the tooltip (`headroom · 26.67% left`).

## Tuning without regenerating

`headroom_tray.draw_icon()` draws the fallback: a dark disc, a ring gauge in the
state colour, and a bold H. Colours come from the same thresholds as the rest of
the UI — blue at 70% and above, amber from 30%, red below. To change the drawn
icon, edit `RING_INSET` and the coordinates in that function; to change the
custom one, just replace the PNG.
