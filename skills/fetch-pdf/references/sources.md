# 站点适配说明

`fetch-pdf` 本身不绑定任何站点。下面记录已适配的来源，以及**给新站点写适配器要看哪几处**。

## 已适配

### 国家中小学智慧教育平台（smartedu.cn / basic.smartedu.cn）

| 项 | 值 |
|---|---|
| 详情页 | `https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=<uuid>&catalogType=tchMaterial&subCatalog=tchMaterial` |
| 元数据（公开、免登录） | `https://s-file-{1,2,3}.ykt.cbern.com.cn/zxx/ndrv2/resources/<type>/details/<contentId>.json` |
| 资源类型（`<type>`） | 教材 = `tch_material`（旧）/ `assets_document`（新，多数教材页现在是这个）；其它：`elite_lesson`、`syncClassroom` |
| 文件实际地址 | `ti_items[].ti_storages[]`，形如 `https://r{1,2,3}-ndr-private.ykt.cbern.com.cn/...pdf` |
| 是否需要登录 | 常见为**需要**（私有 CDN，无签名头返回 401）；部分资源公开 |
| 鉴权方式 | 自定义头 `X-ND-AUTH`，值为 HTTP MAC（`id`/`nonce`/`mac`），密钥来自页面 localStorage |
| PDF 特征 | 多为**平台页图拼装**（无文字层、体积大、可打印）；官方原件需登录后在平台内下载 |

**注意**：

- 域名里的 `-private` 不能去掉，去掉会 403。
- 该平台的教材 PDF 是"电子课本"页图，**没有文字层**——下游 `pdf-to-kb` 会自动走 OCR。
- 只有公开阅读权限的内容才应下载；不要把下载后的原文再分发。

## 给新站点写适配器：改三处

1. **发现真实 URL**：在页面 Network 里找 `*.pdf` 或返回 `application/pdf` 的请求；
   理想情况下能找到"元数据 JSON → 直链"的结构（多数平台都有，比抓页面 DOM 稳定得多）。
2. **`fetch_in_page.js` 的 `readToken()`**：凭证在 `localStorage` / `sessionStorage` / cookie / 内存变量？
   只读取、只在本页使用，**不要 return 给调用方**。
3. **`fetch_in_page.js` 的 `authHeader()`**：常见几种签名
   - HTTP MAC（本仓库已实现，`Authorization/MAC` 系）
   - `Bearer <token>`：直接 `Authorization: Bearer ${token}`
   - URL 签名：`?e=<expire>&token=<hmac>`（注意时间戳单位是秒还是毫秒）
   - MD5 拼接：`md5(path + key + ts)` 之类的私有约定

改完先跑 `pfProbe`，**看到 206 再下载**，别拿大文件试错。

## 不适合用本 skill 的情况

- 需要破解 DRM / 加密 PDF（`/Encrypt` 字典）——本仓库不做，也不建议做
- 付费墙后的内容
- 需要账号批量抓取整站（违反站点条款，且对平台不友好）
