# Third-party material

## dimensional-drawing (MIT)

Dimension-line, radius-leader, notes-table and view-title drawing helpers in
`skills/sketch-to-cad/scripts/sketchcad/dims.py` are adapted from
[enesozturk/dimensional-drawing](https://github.com/enesozturk/dimensional-drawing).

```
MIT License

Copyright (c) 2026 Enes Ozturk

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Fonts (SIL Open Font License 1.1)

Bundled in `skills/sketch-to-cad/assets/fonts/`, each with its license file:

- **Outfit** (`outfit-variable.ttf`), Copyright 2021 The Outfit Project
  Authors (https://github.com/Outfitio/Outfit-Fonts). License:
  `outfit-OFL.txt`. The drawing stage cuts a Regular (wght 400) instance in
  memory for PDF output; the file itself is unmodified.
- **Gowun Batang** (`gowun-batang.ttf`), Copyright 2021 The Gowun Batang
  Project Authors (https://github.com/yangheeryu/Gowun-Batang). License:
  `gowun-OFL.txt`.

## three.js (MIT)

The viewer (`skills/sketch-to-cad/assets/viewer/viewer.bundle.js`, inlined into
every generated `viewer/index.html`) bundles [three.js](https://threejs.org/).
The bundle also carries this notice as a legal comment at its end.

```
The MIT License

Copyright © 2010-2026 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

esbuild (MIT) is used only by developers to build the bundle (`tools/`); it is
not distributed with the skill.

## Python dependencies (installed by the user, not redistributed)

`run.py setup` installs these from PyPI into a virtual environment. Licenses
as reported by the installed package metadata (versions pinned in
`skills/sketch-to-cad/requirements-lock.txt`):

| Package | License |
|---|---|
| build123d | Apache-2.0 |
| cadquery-ocp-novtk (OCP, Open CASCADE bindings; OCCT itself is LGPL-2.1 with the Open CASCADE exception) | Apache-2.0 (bindings) |
| trimesh | MIT |
| numpy | BSD-3-Clause (and bundled permissive notices) |
| reportlab | BSD |
| svglib | LGPL-3.0-or-later |
| Pillow | MIT-CMU |
| networkx | BSD-3-Clause |
| rtree | MIT |
| fonttools | MIT |

The full lock file lists further transitive packages under their own licenses.

## Example images

The example keeps its original sketch byte for byte. Source, author and
license are recorded in:

- [examples/saegmuller-telescope/SOURCE.md](examples/saegmuller-telescope/SOURCE.md) (public domain)

The screenshots and recordings in `docs/images/` combine this sketch with renderings
produced by this project.
