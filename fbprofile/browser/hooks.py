# post/v3/browser/hooks.py
import time
from typing import List, Dict, Any

CLEANUP_JS = r"""
(function(keep) {
  try {
    const selectors = [
      "div[data-pagelet^='FeedUnit_']",
      "div[role='article']",
      "div[aria-posinset]"
    ];
    let posts = [];
    for (const sel of selectors) {
      posts = Array.from(document.querySelectorAll(sel));
      if (posts.length >= 10) break;
    }

    const total = posts.length;
    const k = keep || 30;
    if (total <= k) return;

    const removeCount = total - k;
    for (let i = 0; i < removeCount; i++) {
      const el = posts[i];
      if (!el) continue;
      const story = el.closest("[data-testid='fbfeed_story']") || el;
      story.remove();
    }
  } catch (e) {
    // ignore
  }
})(arguments[0]);
"""


def install_early_hook(driver, keep_last: int = 350):
    hook_src = r"""
    (function(){
      if (window.__gqlHooked) return;
      window.__gqlHooked = true;
      window.__gqlReqs = [];
      function headersToObj(h){try{
        if (!h) return {};
        if (h instanceof Headers){const o={}; h.forEach((v,k)=>o[k]=v); return o;}
        if (Array.isArray(h)){const o={}; for (const [k,v] of h) o[k]=v; return o;}
        return (typeof h==='object')?h:{};}catch(e){return {}}
      }
      function pushRec(rec){try{
        const q = window.__gqlReqs; q.push(rec);
        if (q.length > __KEEP_LAST__) q.splice(0, q.length - __KEEP_LAST__);
      }catch(e){}}
      const origFetch = window.fetch;
      window.fetch = async function(input, init){
        const url = (typeof input==='string') ? input : (input&&input.url)||'';
        const method = (init&&init.method)||'GET';
        const body = (init && typeof init.body==='string') ? init.body : '';
        const hdrs = headersToObj(init && init.headers);
        let rec = null;
        if (url.includes('/api/graphql/') && method==='POST'){
          rec = {kind:'fetch', url, method, headers:hdrs, body:String(body)};
        }
        const res = await origFetch(input, init);
        if (rec){
          try{ rec.responseText = await res.clone().text(); }catch(e){ rec.responseText = null; }
          pushRec(rec);
        }
        return res;
      };
      const XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(m,u,a){ this.__m=m; this.__u=u; return XO.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function(b){
        this.__b = (typeof b==='string')?b:'';
        this.addEventListener('load', ()=>{
          try{
            if ((this.__u||'').includes('/api/graphql/') && (this.__m||'')==='POST'){
              pushRec({kind:'xhr', url:this.__u, method:this.__m, headers:{}, body:String(this.__b),
                       responseText:(typeof this.responseText==='string'?this.responseText:null)});
            }
          }catch(e){}
        });
        return XS.apply(this, arguments);
      };
    })();
    """.replace("__KEEP_LAST__", str(keep_last))

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": hook_src})
    driver.execute_script(hook_src)


def flush_gql_recs(driver) -> List[Dict[str, Any]]:
    try:
        recs = driver.execute_script(
            """
            const q = window.__gqlReqs || [];
            window.__gqlReqs = [];
            return q;
            """
        )
        if not isinstance(recs, list):
            return []
        return recs
    except Exception:
        return []
