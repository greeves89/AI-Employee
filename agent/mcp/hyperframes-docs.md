# Hyperframes — Video Rendering Guide for Agents

## ALWAYS CALL get_docs FIRST before writing any composition HTML.

## Key Rules
1. Every timed element needs `data-start`, `data-duration`, and `data-track-index`
2. Visible timed elements MUST have `class="clip"`
3. GSAP timelines must be paused and registered on `window.__timelines`:
   ```js
   window.__timelines = window.__timelines || {};
   window.__timelines["composition-id"] = gsap.timeline({ paused: true });
   ```
4. No non-deterministic code: no Date.now(), Math.random(), network fetches
5. Videos use `muted` + separate `<audio>` for audio track
6. Root element needs `data-composition-id`, `data-width`, `data-height`, `data-duration`

## Minimal Working Example (5-second video)
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1920, height=1080" />
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1920px; height: 1080px; overflow: hidden; background: #111; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="5"
       data-width="1920" data-height="1080">
    <h1 id="title" class="clip" data-start="0" data-duration="5" data-track-index="1"
        style="color:#fff; font-size:80px; font-family:sans-serif; position:absolute;
               top:50%; left:50%; transform:translate(-50%,-50%); opacity:0">
      Hello World
    </h1>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    tl.to("#title", { opacity: 1, y: -20, duration: 1 }, 0);
    tl.to("#title", { opacity: 0, duration: 0.5 }, 4);
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
```

## Multi-Scene Videos
Use multiple elements with different data-start values on the same timeline:
- Scene 1: data-start="0" data-duration="5"
- Scene 2: data-start="5" data-duration="7"
- Scene 3: data-start="12" data-duration="6"
Total duration = 18s → set on root: data-duration="18"

## Full Docs
https://hyperframes.heygen.com/introduction
Machine-readable index: https://hyperframes.heygen.com/llms.txt
