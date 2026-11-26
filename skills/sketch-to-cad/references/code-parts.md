# Code parts

A `code` part is a Python file you write that builds one solid with build123d.
Use it only when features cannot express the shape. It runs on the user's
machine with their permissions, so keep it small, deterministic and free of
file, network and process access, and tell the user it exists.

## Model entry

```jsonc
{
  "id": "knob",
  "construction": "code",
  "code": { "file": "parts/knob.py", "sha256": "<64 hex chars, optional>" }
}
```

- `file` is relative to the folder of `model.json` and must stay inside it.
- A code part has **no `features` and no `finish`** (both are rejected). Put
  fillets, chamfers and holes inside the code.
- `sha256` is optional but recommended: record it once the file is final
  (`sha256sum parts/knob.py`). When present, the build refuses a file whose
  hash differs. Every build prints `running code part <id>: <file>
  sha256=<hex>` before running the code, and notes when no sha256 is
  recorded. Never build a `model.json` from an untrusted source without
  reading its code parts.
- Other parts and `placement`, `dimensions` work as usual. For a code part,
  the drawing shows the overall width/depth/height and the `dimensions`
  entries you declare (no per-feature dimensions), so declare the numbers
  that matter.

## The file

`parts/<id>.py` defines `build()` returning one valid build123d solid with
volume > 0, in mm, Z up:

```python
# parts/knob.py
from build123d import Align, Cylinder, Pos, Sphere

def build():
    shaft = Cylinder(radius=4, height=18, align=(Align.CENTER, Align.CENTER, Align.MIN))
    head = Pos(0, 0, 18) * Sphere(radius=12)
    return shaft + head
```

Returning a `Part`, `Solid` or
a builder result is fine as long as it holds exactly one solid.

## What the build checks

- the file imports and `build()` runs (an exception becomes
  `<file>: build() failed: ...`);
- the result is exactly one valid solid with positive volume (several
  disjoint solids are rejected: fuse them or split into parts);
- the same STEP round trip and GLB checks as every part.

Edges: if a declarative `finish` cannot select the edges you need, that is the
reason to use a code part; apply the fillet yourself inside `build()`.
