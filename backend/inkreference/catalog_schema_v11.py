"""Schema 7 catalog fields with explicit schema 6 compatibility columns."""
from __future__ import annotations
import json
import math
from typing import Any

WORK_COLUMNS = ['作品ID','作品','类别','作者','发布日期','用户是否完整体验过','剧情梗概','讨论热度','商业规模','热度权重','关联作品','关联角色','关联cliché']
SCHEMA6_WORK_COLUMNS = list(WORK_COLUMNS)
WORK_COLUMNS = [*WORK_COLUMNS, '作品题材', '作品风格']
GENRES = ('奇幻','科幻','现实','历史','武侠','仙侠','悬疑','爱情','冒险')
STYLES = ('推理','克苏鲁','赛博朋克','蒸汽朋克','黑色幽默','浪漫','史诗')
CHARACTER_COLUMNS = ['人物ID','角色名','别名','角色定位','性格基调','视觉元素设计','初始状态','阶段状态','关联作品','关联cliché']
CLICHE_COLUMNS = ['clichéID','cliché内容','cliché评分','关联作品','关联角色','人设塑造','剧情机制']
COLUMNS = {'works':WORK_COLUMNS,'characters':CHARACTER_COLUMNS,'cliches':CLICHE_COLUMNS}
SCHEMA6_COLUMNS = {'works': SCHEMA6_WORK_COLUMNS, 'characters': list(CHARACTER_COLUMNS), 'cliches': list(CLICHE_COLUMNS)}
JSON_DEFAULTS = {'别名':'[]','初始状态':'{}','阶段状态':'[]','关联作品':'[]','关联角色':'[]','关联cliché':'[]'}
TRAITS = ('强大','智谋','利他','共情','利己','执念','反差','宿命')
SCORE_FORMULA = '100 × Σ命中唯一作品热度 / Σ全库唯一作品热度；热度=(讨论热度+商业规模)/2；缺失分项按1'

def dumps(value:Any)->str:
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def is_current(table:str,values:dict)->bool:
    return bool({'works':'剧情梗概','characters':'角色名','cliches':'cliché评分'}[table] in values)

def project_values(table:str,values:dict[str,str])->dict[str,str]:
    """No discarded source field is guessed; callers archive originals separately."""
    result={k:values.get(k,JSON_DEFAULTS.get(k,'')) for k in COLUMNS[table]}
    if table=='works':
        result['剧情梗概']=values.get('剧情梗概',values.get('两句话概括',''))
        result['热度权重']=values.get('热度权重',values.get('热度',''))
    elif table=='characters':
        result['角色名']=values.get('角色名',values.get('角色',''))
        if not is_current(table,values):
            # Free-form historical traits are archived rather than forced into a tag.
            trait=values.get('性格基调',values.get('性格',''))
            result['性格基调']=trait if trait in TRAITS else ''
            result['初始状态']=dumps({'gender':None,'age':'','identity':values.get('开场身份',''),'ability':values.get('开场能力／专长',''),'personality':values.get('性格',''),'appearance':'','relationships':[],'narrative_progress':0,'story_time':None})
            result['阶段状态']='[]'
    return result

def heat_component(value:str)->float|None:
    """A range is stored as JSON; its midpoint is used in weighted scores."""
    if not value:return None
    try:
        item=json.loads(value)
        if isinstance(item,dict):
            lo,hi=item.get('min'),item.get('max')
            if isinstance(lo,bool) or isinstance(hi,bool):return None
            lo,hi=float(lo),float(hi)
            if not (math.isfinite(lo) and math.isfinite(hi) and 1<=lo<=hi<=10):return None
            return (lo+hi)/2
        if isinstance(item,bool):return None
        number=float(item)
    except (TypeError,ValueError):
        try:number=float(value)
        except (TypeError,ValueError):return None
    return number if math.isfinite(number) and 1<=number<=10 else None

def work_heat(values:dict[str,str])->tuple[float,bool]:
    discussion=heat_component(values.get('讨论热度','')); market=heat_component(values.get('商业规模',''))
    if discussion is None and market is None:
        legacy=heat_component(values.get('热度权重',values.get('热度','')))
        if legacy is not None:return legacy,False
    return ((discussion or 1)+(market or 1))/2,discussion is None or market is None
