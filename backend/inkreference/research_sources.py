"""Explicit web-search credentials and independently fetched source excerpts."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from .database import DataError
from .diagnostics import logged_operation, record_exception
from .models import ModelService, _now

DEFAULT_ENDPOINTS={"tavily":"https://api.tavily.com/search","brave":"https://api.search.brave.com/res/v1/web/search","searxng":"http://127.0.0.1:8080/search"}


def _fingerprint(config:dict)->str:
    return hashlib.sha256(json.dumps({k:config[k] for k in ("provider","endpoint","credential_mode","key_env")},sort_keys=True).encode()).hexdigest()


def _endpoint(value:str,*,allow_local:bool=False)->str:
    try:
        p=urlsplit(value)
        if not p.hostname or p.username or p.password or p.query or p.fragment or "\\" in value or any(c.isspace() for c in value):
            raise ValueError
        p.port
        local=p.hostname=="localhost"
        try: local=local or ipaddress.ip_address(p.hostname).is_loopback
        except ValueError: pass
        if p.scheme!="https" and not (allow_local and local and p.scheme=="http"):
            raise ValueError
    except (ValueError,TypeError):
        raise DataError(422,"搜索地址需要 HTTPS；本地 SearXNG 可使用 loopback HTTP") from None
    return value.rstrip("/")


class _Readable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","noscript","svg"}: self.hidden+=1
    def handle_endtag(self,tag):
        if tag in {"script","style","noscript","svg"}: self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())


class ResearchSources:
    def __init__(self,models:ModelService,*,transport=None,resolver=None):
        self.models=models; self.transport=transport
        self.resolver=resolver or socket.getaddrinfo
        self._session=None; self._lock=threading.RLock()
        with models._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS research_settings (id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL, vault_binding TEXT)")

    def _config(self):
        with self.models._connect() as db: row=db.execute("SELECT config_json,vault_binding FROM research_settings WHERE id=1").fetchone()
        if row: return json.loads(row[0]),row[1]
        return {"provider":"tavily","endpoint":DEFAULT_ENDPOINTS["tavily"],"credential_mode":"session","key_env":""},None

    def settings(self):
        config,binding=self._config(); fp=_fingerprint(config)
        if config["credential_mode"]=="session": available=bool(self._session and self._session[0]==fp)
        elif config["credential_mode"]=="environment": available=bool(os.environ.get(config["key_env"]))
        else: available=binding==fp
        return {**config,"key_available":available or config["provider"]=="searxng","target_fingerprint":fp}

    def save_settings(self,values:dict):
        if not isinstance(values,dict) or set(values)-{"provider","endpoint","credential_mode","key_env","api_key"}:
            raise DataError(422,"搜索配置包含未知字段")
        provider=values.get("provider")
        if provider not in DEFAULT_ENDPOINTS: raise DataError(422,"搜索服务仅支持 Tavily、Brave 或 SearXNG")
        endpoint=_endpoint(values.get("endpoint") or DEFAULT_ENDPOINTS[provider],allow_local=provider=="searxng")
        mode=values.get("credential_mode","session")
        if mode not in {"session","vault","environment"}: raise DataError(422,"搜索密钥保存方式无效")
        env=values.get("key_env","") if mode=="environment" else ""
        if not isinstance(env,str) or (mode=="environment" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",env)): raise DataError(422,"请填写有效的搜索密钥环境变量")
        key=values.get("api_key")
        if key is not None and (not isinstance(key,str) or len(key)>10000 or any(ord(c)<32 for c in key)): raise DataError(422,"搜索 API Key 格式无效")
        if mode=="environment" and key: raise DataError(422,"环境变量模式请仅填写变量名")
        config={"provider":provider,"endpoint":endpoint,"credential_mode":mode,"key_env":env}; fp=_fingerprint(config)
        previous,old_binding=self._config(); old_fp=_fingerprint(previous)
        binding=old_binding if old_binding==fp else None
        with self._lock:
            if key is not None:
                if mode=="vault":
                    if key: self.models.vault.set("research-search",fp,key); binding=fp
                    else: self.models.vault.delete("research-search",fp); binding=None
                elif mode=="session": self._session=(fp,key) if key else None
            elif old_fp!=fp: self._session=None
            with self.models._connect() as db: db.execute("INSERT OR REPLACE INTO research_settings VALUES (1,?,?)",(json.dumps(config),binding))
        return self.settings()

    def _key(self,config):
        current,binding=self._config(); fp=_fingerprint(config)
        if _fingerprint(current)!=fp: raise DataError(409,"搜索连接已改变，请重新建立联网任务")
        if config["credential_mode"]=="session": key=self._session[1] if self._session and self._session[0]==fp else None
        elif config["credential_mode"]=="environment": key=os.environ.get(config["key_env"])
        else: key=self.models.vault.get("research-search",fp) if binding==fp else None
        if not key and config["provider"]!="searxng": raise DataError(422,"搜索服务缺少 API Key，请先配置搜索连接")
        return key

    @logged_operation("web", "search", lifecycle=False)
    def search(self,query:str,config:dict)->list[dict]:
        key=self._key(config); provider=config["provider"]
        try:
            with httpx.Client(transport=self.transport,timeout=30,follow_redirects=False,trust_env=False) as client:
                if provider=="tavily": response=client.post(config["endpoint"],headers={"Authorization":"Bearer "+key},json={"query":query,"max_results":5,"search_depth":"basic","include_answer":False,"include_raw_content":False})
                elif provider=="brave": response=client.get(config["endpoint"],headers={"X-Subscription-Token":key},params={"q":query,"count":5})
                else: response=client.get(config["endpoint"],params={"q":query,"format":"json","language":"zh-CN"})
            if not 200<=response.status_code<300:
                error=DataError(502,f"搜索服务返回 HTTP {response.status_code}，未完成联网检索")
                record_exception("web","search",error,status=f"provider_http_{response.status_code}",http_status=response.status_code)
                raise error
            if len(response.content)>2_000_000: raise DataError(502,"搜索响应超过大小限制")
            data=response.json(); rows=data.get("web",{}).get("results",[]) if provider=="brave" else data.get("results",[])
            if not isinstance(rows,list): raise ValueError
            result=[]
            for row in rows[:5]:
                if not isinstance(row,dict) or not isinstance(row.get("url"),str): continue
                result.append({"url":row["url"],"title":str(row.get("title", ""))[:500],"snippet":str(row.get("content",row.get("description","")))[:1500]})
            return result
        except DataError: raise
        except Exception as error:
            record_exception("web","search",error)
            raise DataError(502,"搜索请求失败，未完成联网检索；请检查服务地址及密钥") from None

    def _public_url(self,url):
        try:
            p=urlsplit(url)
            if p.scheme not in {"https","http"} or not p.hostname or p.username or p.password or "\\" in url: raise ValueError
            port=p.port or (443 if p.scheme=="https" else 80)
            addresses=self.resolver(p.hostname,port,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise ValueError
        except Exception: raise DataError(422,"来源网址指向本地或私有网络，已跳过") from None
        return url

    def fetch(self,row:dict)->dict:
        source={"id":"src_"+hashlib.sha256(row["url"].encode()).hexdigest()[:16],"url":row["url"],"title":row["title"],"excerpt":"","search_excerpt":row.get("snippet",""),"fetch_status":"failed","fetched_at":_now(),"error":None}
        http_status=None
        try:
            url=row["url"]
            with httpx.Client(transport=self.transport,timeout=20,follow_redirects=False,trust_env=False,headers={"User-Agent":"InkReference/0.2 research (+user initiated)"}) as client:
                for redirects in range(6):
                    self._public_url(url)
                    with client.stream("GET",url) as response:
                        http_status=response.status_code
                        if response.status_code in {301,302,303,307,308}:
                            location=response.headers.get("location")
                            if not location or redirects==5: raise DataError(502,"来源重定向超过限制")
                            url=urljoin(url,location); continue
                        if response.status_code!=200: raise DataError(502,f"来源返回 HTTP {response.status_code}")
                        content_type=response.headers.get("content-type","").lower()
                        if not any(t in content_type for t in ("text/html","text/plain","application/xhtml")): raise DataError(502,"来源不是可读取的网页文本")
                        chunks=[]; size=0
                        for chunk in response.iter_bytes():
                            size+=len(chunk)
                            if size>2_000_000: raise DataError(502,"来源页面超过大小限制")
                            chunks.append(chunk)
                        raw=b"".join(chunks).decode(response.encoding or "utf-8",errors="replace")
                    if "html" in content_type:
                        parser=_Readable(); parser.feed(raw); text="\n".join(parser.parts)
                    else: text=raw
                    text=re.sub(r"[ \t]+"," ",text).strip()
                    if len(text)<40: raise DataError(502,"来源正文过短或需要登录，未完成核实")
                    source.update(excerpt=text[:8000],fetch_status="verified",final_url=url,content_hash=hashlib.sha256(text.encode()).hexdigest())
                    return source
        except DataError as exc:
            record_exception("web","fetch",exc,data_dir=self.models.data_dir,stage="source",status="skipped",http_status=http_status,level="WARNING")
            source["error"]=str(exc.detail)
        except Exception as error:
            record_exception("web","fetch",error,data_dir=self.models.data_dir,stage="source",status="skipped",http_status=http_status,level="WARNING")
            source["error"]="无法读取来源网页"
        return source
