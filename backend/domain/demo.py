from datetime import datetime, timezone
from random import Random

from sqlalchemy import select

from core.db import transaction
from domain.channel import patch_mutex
from domain.review_tables import review_batches, review_windows
from domain.interventions import findings
from domain.soundboard import points, windows
from domain.states import FEATURES

PLACES = [
    ("MIC-PAV","东门棚台","TAG-E01","BUS-A"),
    ("MIC-WALK","西风雨连廊","TAG-W09","BUS-A"),
    ("MIC-INFO","入口咨询桌","TAG-Z03","BUS-A"),
    ("MIC-HUB","室内总音控","TAG-N26","BUS-B"),
]


def capture(code, location, slot, frequency, levels, purpose="live"):
    channel=next(item[3] for item in PLACES if item[0]==location)
    return {"code":code,"point":location,"channel":channel,"revision":1,"slot":slot,
            "frequency":frequency,"purpose":purpose,**dict(zip(FEATURES,levels,strict=True))}


def bench():
    rng=Random(217)
    return [capture(f"REF-{index+1:02}",PLACES[index%4][0],index+1,550+index*13,
                    [max(.03,min(.42,rng.gauss(center,.033))) for center in (.20,.24,.30,.15)],"baseline")
            for index in range(32)]


# 漏报复核专用声检窗（覆盖命中、漏报、虚警、一致正常四类情形）
REVIEW_WINDOW_CODES=("WIN-AUD-M1","WIN-AUD-M2","WIN-AUD-F1","WIN-AUD-OK")
review_windows_seed=[
    # 机器判为啸叫（local）：一名复核员将听到啸叫（命中）
    capture("WIN-AUD-M1","MIC-HUB",90,2310,(.88,.82,.60,.66)),
    # 机器判正常、现场实为啸叫：这是要抓的“漏报”
    capture("WIN-AUD-M2","MIC-PAV",91,1840,(.18,.20,.28,.16)),
    # 机器判为啸告、现场无啸叫：虚警
    capture("WIN-AUD-F1","MIC-INFO",92,1290,(.85,.76,.58,.60)),
    # 机器判正常、现场也正常
    capture("WIN-AUD-OK","MIC-WALK",93,680,(.20,.22,.30,.14)),
]


def finding(code, window, category, score, token, shared=None):
    return {"code":code,"focus_window":window,"category":category,"score":score,"neighbors":8,
            "shared_points":shared or [],"reviewed_by":"reviewer@demo.test","compute_token":token}


review_findings_seed=[
    finding("FD-AUD-M1","WIN-AUD-M1","local",-2.14,"a1b2c3d4e5f60718293a4b5c6d7e8f90"),
    finding("FD-AUD-M2","WIN-AUD-M2","normal",-0.42,"b2c3d4e5f60718293a4b5c6d7e8f90a1"),
    finding("FD-AUD-F1","WIN-AUD-F1","coordinated",-1.91,"c3d4e5f60718293a4b5c6d7e8f90a1b2",
            ["MIC-INFO","MIC-PAV","MIC-WALK"]),
    finding("FD-AUD-OK","WIN-AUD-OK","normal",-0.31,"d4e5f60718293a4b5c6d7e8f90a1b2c3"),
]


FIXTURES=(
    (patch_mutex,[{"code":1}]),
    (points,[{"code":code,"location":name,"mic_tag":tag,"channel":channel} for code,name,tag,channel in PLACES]),
    (windows,bench()+[
        capture("WIN-C-01","MIC-PAV",80,1320,(.79,.73,.55,.59)),
        capture("WIN-C-02","MIC-WALK",80,1324,(.80,.72,.57,.53)),
        capture("WIN-C-03","MIC-INFO",80,1311,(.83,.74,.56,.57)),
        capture("WIN-L-01","MIC-HUB",82,2210,(.91,.87,.62,.70)),
        capture("WIN-N-01","MIC-WALK",84,730,(.22,.25,.31,.15)),
    ]+review_windows_seed),
    (findings,review_findings_seed),
)


def review_seed():
    """预置一个漏报复核批次（窗与机器判定），听音与封存留待用户操作。"""
    machine_howl={"local":1,"coordinated":1,"normal":0}
    with transaction() as conn:
        if conn.execute(select(review_batches.c.code).where(review_batches.c.code=="RV-DEMO-01")).first():
            return
        conn.execute(review_batches.insert().values(
            code="RV-DEMO-01", created_by="reviewer@demo.test",
            created_at=datetime.now(timezone.utc)))
        for item in review_findings_seed:
            conn.execute(review_windows.insert().values(
                batch="RV-DEMO-01", finding=item["code"], window=item["focus_window"],
                machine_howl=machine_howl[item["category"]]))

