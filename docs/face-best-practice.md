# Keep the face: best practice for img-edit (FLUX.2 klein 4B)

Tested 2026-10-26 with an ArcFace face-similarity score (1.0 = same image).
Real photos of the same person score about 0.92 against each other. That is the ceiling.

## Results

| Test | Score |
| --- | --- |
| Short prompt ("the same person, in a suit, on a street") | 0.57 |
| Explicit "keep the face exactly" prompt | 0.69 |
| Same, plus "head and shoulders" | 0.68 |
| Same, plus film/skin-texture words | 0.66 |
| 8 steps instead of 4 | 0.66 (2x slower) |
| Own photo, edit clothes only, same pose, 1 reference | 0.88 - 0.90 |
| Same, 2 references | 0.85 - 0.87 (1.6x slower) |
| Same, 4 references | 0.77 - 0.80 (4x slower) |
| Same, 1 reference, 768x1664 | 0.89 - 0.90 |

## Rules

1. Use one sharp, frontal, well-lit reference photo. More references made it worse.
2. Edit in place: keep the pose, angle, framing and background of the photo. Change only
   what you need (clothes). New angles force the model to invent unseen parts of the face.
3. The reference must show the neck. A tight, chin-down selfie gives a shirt collar
   right under the beard (no neck, head looks pasted on). Prompts did not fix it in
   3 tries; a photo with the neck visible fixed it at once.
   Add: "keep his natural neck visible between the beard and the shirt collar,
   shoulders natural width and in proportion to the head".
4. Match the output shape to the photo (phone selfie 1080x2340 -> `-W 768 -H 1664`).
5. Say what to keep, in detail: "Keep the man's face exactly as in the reference image:
   same face shape, eyes, eyebrows, nose, lips, beard, skin tone, skin texture, hairline
   and hair. Keep the same head pose, expression, camera angle, framing, lighting and background."
6. Say what to change first and be concrete ("Replace only the white t-shirt with ...").
7. Against the AI look: "Real smartphone photo, no retouching". Avoid "8k, ultra detailed, perfect skin".
8. Keep 4 steps. Try 2-4 seeds (`-S 1,2,3`, one model load) and pick the best.

## Template

```bash
img-edit "Replace only the <what> with <new>. Keep the <man/woman>'s face exactly as in the reference image: same face shape, eyes, eyebrows, nose, lips, beard, skin tone, skin texture, hairline and hair. Keep the same head pose, expression, camera angle, framing, lighting and background. Real smartphone photo, no retouching." refs/photo.jpg -W 768 -H 1664 -S 1
```
