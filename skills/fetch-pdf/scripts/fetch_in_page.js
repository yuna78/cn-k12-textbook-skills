// fetch_in_page.js — 登录态受限资源：在页面内取数（token 不出浏览器）
//
// 适用场景：目标 PDF 在私有 CDN 上，直接用 curl 会 401；但用户**已经在浏览器里登录**，
// 页面 localStorage 里有会话凭证。此时把本文件注入到该站点的页面里执行，
// 用页面自己的凭证签名 / 直接 fetch，再把字节交给浏览器保存。
//
// 为什么必须在页面内：
//   1) 凭证在页面 origin 的 localStorage 里，导入模型 = 泄密风险；留在页面则永不出浏览器
//   2) 私有 CDN 的 CORS 只信任该 origin，外部请求会被拦
//
// 注入后用两个全局函数：
//   await window.pfProbe(url, {headers})        → {status, ctype}（1KB 探测，206/200 = 通了）
//   window.pfDownload(url, filename, {headers}) → "kicked off"；进度看 window.__dl
//
// 非通用部分（每个站点不同）：凭证从哪里取、签名怎么算。本文件给的是
// 国家中小学智慧教育平台（smartedu / ykt.cbern.com.cn）的 MAC 方案，其它站点请替换
// `readToken()` 与 `authHeader()` 两个函数——其余逻辑（流式下载、Blob 保存、进度）通用。

(() => {
  // ---- 站点相关：凭证读取（smartedu 版）------------------------------------
  function readToken() {
    // localStorage 里形如 "ND_UC_AUTH-xxx&token" 的键，值是 {access_token, mac_key, ...}
    const key = Object.keys(localStorage).find(
      x => x.includes("ND_UC_AUTH") && x.endsWith("token"));
    if (!key) throw new Error("localStorage 里没有登录凭证 —— 请先在本页登录，然后刷新页面");
    let val = JSON.parse(localStorage.getItem(key)).value;
    if (typeof val === "string") val = JSON.parse(val);
    if (!val.access_token || !val.mac_key) throw new Error("凭证存在但缺少 access_token/mac_key");
    return val;
  }

  // ---- 站点相关：签名（smartedu 用 HTTP MAC，RFC 草案变体）------------------
  async function hmacB64(keyStr, msg) {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw", enc.encode(keyStr), {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
    return btoa(String.fromCharCode(...new Uint8Array(sig)));
  }

  function rand(n) {
    const cs = "abcdefghijklmnopqrstuvwxyz0123456789";
    return Array.from({length: n}, () => cs[Math.floor(Math.random() * cs.length)]).join("");
  }

  // 归一化串：nonce \n METHOD \n uri \n host \n port \n ext \n  （ext 为空 → 结尾两个 \n）
  async function authHeader(url) {
    const t = readToken();
    const u = new URL(url);
    const nonce = Math.floor(Date.now() / 1000) + ":" + rand(8);
    // 注意：签名覆盖的 path 必须与真正发出去的完全一致（用 encodeURI 而不是 encodeURIComponent）
    const encPath = encodeURI(decodeURI(u.pathname));
    const normalized = nonce + "\nGET\n" + encPath + "\n" + u.hostname + "\n" +
      (u.port || "443") + "\n\n";
    const mac = await hmacB64(t.mac_key, normalized);
    return 'MAC id="' + t.access_token + '",nonce="' + nonce + '",mac="' + mac + '"';
  }
  // ------------------------------------------------------------------------

  window.pfProbe = async function (url, opts = {}) {
    const headers = {...(opts.headers || {})};
    if (opts.auth !== false) headers["X-ND-AUTH"] = await authHeader(url);
    headers["Range"] = headers["Range"] || "bytes=0-1023";
    const resp = await fetch(url, {method: "GET", headers});
    const out = {status: resp.status, ctype: resp.headers.get("content-type") || ""};
    try { resp.body.cancel(); } catch (e) {}
    return JSON.stringify(out);
  };

  window.pfDownload = function (url, filename, opts = {}) {
    window.__dl = {status: "starting", received: 0, total: 0, done: false, error: null};
    (async () => {
      try {
        const headers = {...(opts.headers || {})};
        if (opts.auth !== false) headers["X-ND-AUTH"] = await authHeader(url);
        window.__dl.status = "fetching";
        const resp = await fetch(url, {method: "GET", headers});
        window.__dl.httpStatus = resp.status;
        if (!resp.ok) {
          window.__dl.status = "http_error";
          window.__dl.error = "HTTP " + resp.status;
          return;
        }
        window.__dl.total = Number(resp.headers.get("content-length") || 0);
        const reader = resp.body.getReader();
        const chunks = [];
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          chunks.push(value);
          window.__dl.received += value.length;
        }
        window.__dl.status = "saving";
        const blob = new Blob(chunks, {type: opts.mime || "application/pdf"});
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
          try { document.body.removeChild(a); URL.revokeObjectURL(objUrl); } catch (e) {}
        }, 60000);
        window.__dl.status = "clicked";
        window.__dl.done = true;
      } catch (e) {
        window.__dl.status = "exception";
        window.__dl.error = String(e).slice(0, 200);
      }
    })();
    return "kicked off; 用 window.__dl 看进度";
  };

  return "pfProbe / pfDownload 已定义";
})()
