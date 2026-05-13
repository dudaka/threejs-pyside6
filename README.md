# threejs-pyside6

A PySide6 desktop app hosting a Three.js scene via `QWebEngineView`. Foundation for visualizing the Silkroad Online map with pathfinding and many entities.

## Run

```bash
uv sync
uv run python main.py        # procedural terrain demo
uv run python bench.py       # render-throughput benchmark
uv run python bridge.py      # Python <-> JS bridge with InstancedMesh agents
uv run python sweep.py       # parameter sweep over bridge.py configs
```

`bridge.py` takes `--n <entities>`, `--hz <target rate>`, `--protocol {json,bytes}`, `--seconds <auto-quit>`.

## Layout

- `main.py` — desktop window + loopback HTTP server serving `web/`.
- `bench.py` / `sweep.py` — benchmark harnesses.
- `bridge.py` — `QWebChannel` bridge for entity updates.
- `web/index.html` — procedural terrain scene.
- `web/bench.html`, `web/bridge.html` — scenes for the harnesses.
- `web/vendor/three/` — vendored Three.js r184.
- `web/vendor/qwebchannel/` — vendored `qwebchannel.js`.

Drag to orbit, scroll to zoom. See `CLAUDE.md` for architecture notes and gotchas.
