"""Project state + CZI inspection. A project = one CZI + one work directory."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class Project:
    czi: str
    work: str
    ds: int = 4                      # xy downsample for registration / preview
    workers: int = 8
    z_start: int = 0                 # use a z-subset for quick tests
    z_stop: int | None = None
    channels: list[int] | None = None
    reg_channel: int = 0
    use_pp: bool = False             # register/fuse read the preprocessed tiles instead of raw ones
    pp: dict | None = None           # preprocessing parameters (see preprocess.DEFAULT_PARAMS)

    # ---- paths -----------------------------------------------------------
    @property
    def work_dir(self) -> Path:
        p = Path(self.work)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def raw_dir(self) -> Path:
        p = self.work_dir / "tiles"
        p.mkdir(exist_ok=True)
        return p

    @property
    def pp_dir(self) -> Path:
        return self.work_dir / "tiles_pp"

    @property
    def tiles_dir(self) -> Path:
        """Tiles that register/fuse read: preprocessed if enabled, else raw."""
        return self.pp_dir if self.use_pp else self.raw_dir

    @property
    def fused_dir(self) -> Path:
        p = self.work_dir / "fused"
        p.mkdir(exist_ok=True)
        return p

    @property
    def info_path(self) -> Path:
        return self.work_dir / "info.json"

    @property
    def reg_path(self) -> Path:
        return self.work_dir / "registration.json"

    # ---- persistence -----------------------------------------------------
    def save(self):
        (self.work_dir / "project.json").write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, work: str) -> "Project":
        return cls(**json.loads((Path(work) / "project.json").read_text()))

    # ---- derived ---------------------------------------------------------
    def info(self) -> dict:
        if self.info_path.exists():
            return json.loads(self.info_path.read_text())
        return inspect_czi(self)

    def z_range(self) -> tuple[int, int]:
        nz = self.info()["nz"]
        return self.z_start, (nz if self.z_stop is None else min(self.z_stop, nz))

    def channel_list(self) -> list[int]:
        return self.channels if self.channels is not None else list(range(self.info()["nc"]))


def inspect_czi(proj: Project) -> dict:
    """Read the CZI directory + metadata only (no pixel data)."""
    from aicspylibczi import CziFile

    c = CziFile(proj.czi)
    dims = dict(zip(c.dims, c.size))
    xml = ET.tostring(c.meta, encoding="unicode")

    def scaling(ax):
        m = re.search(rf"<Scaling{ax}>([^<]+)</Scaling{ax}>", xml)
        return float(m.group(1)) * 1e6 if m else None

    ov = re.search(r"<TileAcquisitionOverlap>([^<]+)<", xml)
    chan_names = re.findall(r'<Channel Id="[^"]+" Name="([^"]+)"', xml)
    seen, names = set(), []
    for n in chan_names:
        if n not in seen:
            seen.add(n)
            names.append(n)

    tiles = {}
    for ti, bb in c.get_all_tile_bounding_boxes().items():
        dc = ti.dimension_coordinates
        if dc.get("Z", 0) == 0 and dc.get("C", 0) == 0:
            tiles[ti.m_index] = dict(m=ti.m_index, x=bb.x, y=bb.y, w=bb.w, h=bb.h)
    def num(pat):
        m = re.search(pat, xml)
        return float(m.group(1)) if m else None

    info = dict(
        czi=proj.czi,
        sheet_um=num(r'Name="LightSheetThickness"[^>]*>([^<]+)<'),
        na=num(r"EC Plan-Neofluar [\d.]+x/([\d.]+)"),
        emission_nm=num(r"<EmissionWavelength>([^<]+)<"),
        nz=dims.get("Z", 1), nc=dims.get("C", 1), nt=dims.get("T", 1),
        ntiles=len(tiles),
        px_xy_um=scaling("X"), px_z_um=scaling("Z"),
        overlap=float(ov.group(1)) if ov else None,
        channel_names=names[: dims.get("C", 1)],
        tiles=[tiles[k] for k in sorted(tiles)],
    )
    proj.info_path.write_text(json.dumps(info, indent=2))
    return info
