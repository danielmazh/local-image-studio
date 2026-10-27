# How to use it - examples

Tell the director what you want, attach a photo if you have one, and it makes the image right away. Every example here was made with this system, with fictional people.

## 1. A new image from text

No photo attached. The model: Z-Image (best for realistic people).

```try
A woman in her 30s with short black hair and a denim jacket, sitting by a window in a cafe, soft daylight
```

![A new image from text](docs/examples/gen-portrait.jpg)

Tip: describe who, what they do, where and what light. The director fills in the rest.

## 2. Text inside the image (sign, poster, logo)

In Settings > Models > Text to image, pick **FLUX.2 klein 9B (Ollama, fp8)**. Left: Z-Image, right: 9B. Only 9B wrote the sign correctly.

```try
A speaker next to a wooden lectern with a small copper plaque that says "OPEN SCIENCE 2026", warm stage light
```

![Z-Image vs FLUX.2 klein 9B](docs/examples/text-compare.jpg)

## 3. Edit in place: change the clothes and keep the face

Attach a photo and say what to change. This is the most accurate for faces (likeness score about 0.90): same pose, only the clothes change.

```try
Change my shirt to a dark blue blazer with a white shirt, keep everything else the same
```

![The original photo](docs/examples/ref-woman.jpg) ![After the edit](docs/examples/edit-clothes.jpg)

Tip: a sharp photo from the front, with neck and shoulders. A close selfie with the chin down gives a collar under the beard.

## 4. The same person in a new scene

Attach a photo and describe a new place. The face is kept less well than with an edit in place (about 0.5), because the model changes the pose and the angle.

```try
The same woman giving a talk on a conference stage, next to a big screen, a blurred audience in front
```

![The original photo](docs/examples/ref-woman.jpg) ![In a new scene](docs/examples/new-scene.jpg)

The studio fixes the face by itself: a face pass from your photo, then a face match score. Below 90% it makes up to 2 more rounds.

Tip: if the face is still not accurate, write "replace only the face based on my photo" and attach the original photo again.

## 5. Swap a face in an existing scene (face swap)

Attach two photos in this order: **1 = the scene**, **2 = the face**. Say whose face to swap.

```try
Image 1 is the scene. Replace the face of the man on the right with the face from image 2, everything else exactly the same
```

![1 - the scene](docs/examples/swap-scene.jpg) ![2 - the face](docs/examples/swap-face.jpg)

![The result](docs/examples/swap-result.jpg)

Tip: the head angle in image 2 should be close to the angle in the scene. Use only photos of people who agreed.

## 6. Rounds: improve without starting over

When the image is ready, just keep writing. The director changes only what you asked for and makes it again.

```try
Darker, evening light
```

```try
The same, but with an office in the background
```

Press **Director review** to get up to 4 points from it: what is good, what to fix, and which image is the best.

## 7. The settings (the "Settings" button next to the text box)

- **Images 1-4**: how many versions to make. Each one takes about 40-90 seconds.
- **Refine**: off = only your words. 1 = tidies and clarifies. 2 = fills in missing details. 3 = creative, adds ideas.
- **Models**: chat, text to image, edit. A model that is not installed shows in gray; install it on the Models page.
- **Stop**: stops at any moment. Images that are already made stay.

## 8. Prompt builder: work on the prompt first

The **Prompt Builder** tab is a chat only. It makes no images. With **Prompt builder mode** on, it builds one English prompt with you and shows it in a box. Press **Send to Studio** to open a new Studio tab with the prompt in the message box, or **Copy**.

```text
A poster for a jazz night in an old port town, warm evening light, the title "Blue Harbor" in big letters
```

Then keep writing what to change, for example "more rain" or "a wider shot". With the switch off, it is a plain language model with no role: ask it anything.

Also there: **Thinking** (see what it thinks before it answers, slower), a model picker, and **Chats** to reopen a saved chat.
