import json

import pytest

from inkreference.database import DataError
from inkreference.documents import DocumentStore
from inkreference.volumes import VolumeStore, detect_volumes, parse_volume_paste, validate_volumes


def test_upstream_markers_and_preface_coverage():
    chapters = [{'title':'序言','text':'前言\n'}, {'title':'第一章','text':'第一卷 春雨\n第一章 相逢\n'}, {'title':'第二章','text':'正文\n'}, {'title':'第三章','text':'第二卷 夏雷\n第三章 再会\n'}]
    assert detect_volumes(chapters) == [{'title':'序篇','start_chapter':1,'end_chapter':1},{'title':'第一卷 春雨','start_chapter':2,'end_chapter':3},{'title':'第二卷 夏雷','start_chapter':4,'end_chapter':4}]


@pytest.mark.parametrize('volumes', [[], [{'title':'遗漏','start_chapter':2,'end_chapter':3}], [{'title':'超出','start_chapter':1,'end_chapter':4}], [{'title':'重叠1','start_chapter':1,'end_chapter':2},{'title':'重叠2','start_chapter':2,'end_chapter':3}], [{'title':'布尔','start_chapter':True,'end_chapter':3}]])
def test_bad_plans_never_silently_repaired(volumes):
    with pytest.raises(DataError): validate_volumes(volumes, 3)


def test_paste_version_pinning_and_revision(tmp_path):
    documents=DocumentStore(tmp_path)
    doc=documents.upload('book.txt','第一章 春雨\n相逢。\n第二章 夏雷\n再会。\n'.encode())
    store=VolumeStore(tmp_path,documents)
    vid=doc['latest_version_id']
    value=[{'title':'春夏','start_chapter':1,'end_chapter':2}]
    pasted=parse_volume_paste('```json\n'+json.dumps({'volumes':value},ensure_ascii=False)+'\n```',2)
    assert store.save(doc['id'],vid,pasted,0)['revision']==1
    with pytest.raises(DataError):store.save(doc['id'],vid,pasted,0)
    nextversion=documents.create_version(doc['id'],vid,{},manual_text='第一章 冬雪\n初见。\n')
    assert store.get(doc['id'],vid)['volumes']==value
    assert store.get(doc['id'],nextversion['id'])['revision']==0
    assert '#1: 春雨' in store.prompt(doc['id'],vid)['prompt']
