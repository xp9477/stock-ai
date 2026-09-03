"""Research hypothesis, grid-search, and promotion API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db

research_router = APIRouter()

class HypothesisCreate(BaseModel):
    theory_text: str
    title: str = ""


class SpecUpdate(BaseModel):
    spec: dict
    confirm: bool = False


class DiscardBody(BaseModel):
    reason: str = ""


class ResearchBacktestBody(BaseModel):
    years: int = 3
    reveal_holdout: bool = True
    actor: str = "local_user"


class HoldoutOpenBody(BaseModel):
    actor: str = "local_user"


@research_router.get("/research/hypotheses")
def research_list(status: str | None = None, db: Session = Depends(get_db)):
    from ..research import service as research

    return {"items": research.list_hypotheses(db, status=status)}


@research_router.post("/research/hypotheses")
def research_create(body: HypothesisCreate, db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.create_hypothesis(db, body.theory_text, title=body.title or "")
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@research_router.get("/research/hypotheses/{hid}")
def research_get(hid: int, db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        h = research.get_hypothesis(db, hid)
        return research._to_dict(h)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err


@research_router.post("/research/hypotheses/{hid}/translate")
def research_translate(hid: int, db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.translate(db, hid)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, str(err)) from err


@research_router.put("/research/hypotheses/{hid}/spec")
def research_update_spec(hid: int, body: SpecUpdate, db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.update_spec(db, hid, body.spec, confirm=body.confirm)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@research_router.post("/research/hypotheses/{hid}/backtest")
def research_backtest(hid: int, body: ResearchBacktestBody = ResearchBacktestBody(),
                      db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.run_backtest(
            db,
            hid,
            years=body.years,
            reveal_holdout=body.reveal_holdout,
            actor=body.actor,
        )
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    except Exception as err:  # noqa: BLE001
        logger = __import__("logging").getLogger(__name__)
        logger.exception("research backtest")
        raise HTTPException(502, str(err)) from err


@research_router.get("/research/experiments/{experiment_id}")
def research_experiment_get(
    experiment_id: int,
    db: Session = Depends(get_db),
):
    from ..backtest.evidence import experiment_dict
    from ..models import BacktestExperiment

    experiment = db.get(BacktestExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "experiment 不存在")
    # Metadata and development evidence are readable.  Holdout stays absent
    # from this endpoint even after it has previously been opened.
    return experiment_dict(db, experiment, include_holdout=False)


@research_router.post(
    "/research/hypotheses/{hid}/experiments/{experiment_id}/holdout/open")
def research_holdout_open(
    hid: int,
    experiment_id: int,
    body: HoldoutOpenBody = HoldoutOpenBody(),
    db: Session = Depends(get_db),
):
    from ..research import service as research

    try:
        return research.reveal_holdout(
            db,
            hid,
            experiment_id,
            actor=body.actor or "local_user",
        )
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@research_router.post("/research/hypotheses/{hid}/promote")
def research_promote(hid: int, db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.promote(db, hid)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@research_router.post("/research/hypotheses/{hid}/discard")
def research_discard(hid: int, body: DiscardBody = DiscardBody(),
                     db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.discard(db, hid, reason=body.reason or "")
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@research_router.post("/research/hypotheses/{hid}/retire")
def research_retire(hid: int, body: DiscardBody = DiscardBody(),
                    db: Session = Depends(get_db)):
    from ..research import service as research

    try:
        return research.retire(db, hid, reason=body.reason or "")
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@research_router.get("/research/library")
def research_library():
    from ..research.library import get_library
    return get_library()


class GridRunBody(BaseModel):
    years: int = 3
    factor_set_ids: list[str] | None = None
    top_n_list: list[int] | None = None
    rebalances: list[str] | None = None
    stop_losses: list[float | None] | None = None
    include_equal_weight: bool = True
    max_combos: int | None = None


class GridImportBody(BaseModel):
    specs: list[dict]
    theory_prefix: str = "网格导入"


class ProposeBody(BaseModel):
    count: int = 5
    mode: str = "library"  # library | improve


@research_router.post("/research/grid/run")
def research_grid_run(body: GridRunBody = GridRunBody(), db: Session = Depends(get_db)):
    """规则库网格批量回测（手动触发，单次共享面板）。"""
    from ..research.grid import run_grid
    from ..runtime_settings import get_setting

    max_c = body.max_combos
    if max_c is None:
        try:
            max_c = int(get_setting("research.grid_max_combos"))
        except Exception:  # noqa: BLE001
            max_c = 48
    try:
        return run_grid(
            db,
            years=body.years,
            factor_set_ids=body.factor_set_ids,
            top_n_list=body.top_n_list,
            rebalances=body.rebalances,
            stop_losses=body.stop_losses,
            include_equal_weight=body.include_equal_weight,
            max_combos=max_c,
        )
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, str(err)) from err


@research_router.post("/research/grid/import")
def research_grid_import(body: GridImportBody, db: Session = Depends(get_db)):
    from ..research.grid import import_specs_as_hypotheses

    if not body.specs:
        raise HTTPException(400, "specs 不能为空")
    items = import_specs_as_hypotheses(
        db, body.specs, theory_prefix=body.theory_prefix or "网格导入",
    )
    return {"ok": True, "imported": len(items), "items": items}


@research_router.post("/research/propose")
def research_propose(body: ProposeBody = ProposeBody(), db: Session = Depends(get_db)):
    """AI/规则库提议假说（B）：生成草稿，须人确认后回测。"""
    from ..research.propose import propose_candidates

    mode = body.mode if body.mode in ("library", "improve") else "library"
    items = propose_candidates(db, count=body.count, mode=mode)
    return {"ok": True, "mode": mode, "items": items}
