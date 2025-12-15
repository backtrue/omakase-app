# **產品需求文件 (PRD) \- Project Omakase v1.0**

user story

使用者拍完一張菜單照片後上傳，系統先把菜單做 OCR 以及利用 GPS 資料找看看資料庫中有沒有同樣或近似 GPS 的圖片或部分圖片，不論有沒有，當下照片的菜單的 OCR 還是要做，因為基於所辨識出來的菜名來去看資料庫中有沒有已經儲存經過翻譯的菜名，如果有未來就直接用。如果沒有就開始翻譯菜名，黃金時間點是 180 秒內要處理完畢，最快希望一分鐘之內就可以出來幾個基本翻譯過的菜以及圖片生成（只有前三個菜會有圖片），其他還沒翻譯的會繼續在後台翻譯，前端操作不影響

## **0\. 文件治理 (Document Governance)**

為了確保後續開發一致性，本文件定義「產品目標與高層契約」，工程細節以 `spec/` 目錄下的規格為準。

### **0.1 規範引用 (Normative References)**

- `spec/00_ARCHITECTURE.md`：Public domain / routing / components
- `spec/01_API_SSE.md`：SSE 事件契約（事件順序、payload schema、keep-alive cadence、錯誤碼）
- `spec/02_STORAGE_R2.md`：資產儲存（R2）、key 與 URL 慣例、cache headers
- `spec/03_CACHE_VECTORIZE.md`：快取分期（SHA256 / Vectorize）與 fail-open 原則
- `spec/04_SECRETS_ENV.md`：環境變數與 secret 管理

## **1\. 產品定義 (Product Definition)**

* **產品代號**：Project Omakase  
* **核心價值**：專為深度旅日饕客打造的「居酒屋解碼器」。透過 AI 推理與視覺化，消除語言隔閡，讓點餐成為文化探索的一部分。  
* **目標客群**：追求在地體驗、不滿足於連鎖店、願意嘗試無圖菜單老店的資深自由行旅客。  
* **成功關鍵**：  
  1. **手寫辨識率**：能解讀連筆、變體假名（Gemini 3 Pro 推理能力）。  
  2. **翻譯信達雅**：不僅翻譯，更要解釋料理脈絡（RAG/Knowledge Base）。  
  3. **視覺風格一致性**：穩定的日式水彩畫風，提供溫暖的點餐體驗。

## **2\. 核心功能流程 (Core User Flow)**

1. **Snap (拍攝)**：  
   * 用戶拍攝整頁手寫菜單。  
   * App 進行透視校正與影像增強。  
2. **Analyze (推理與生成)**：  
   * Backend (Gemini 3 Pro) 進行圖文識別。  
   * 識別同時，邏輯推斷出「Top 3 推薦菜色」。  
   * 針對 Top 3 菜色，平行呼叫 Image Gen 模型 (Gemini 3 Pro Image) 生成水彩插畫。  
3. **Visualize (呈現)**：  
   * **首屏列表**：顯示完整文字菜單（日文 \+ 繁中意譯 \+ 關鍵食材 Tag）。  
   * **視覺焦點**：Top 3 菜色直接展示精美水彩圖，其餘菜色僅顯示 Icon。  
   * **Lazy Loading**：若用戶點擊其他無圖菜色，才觸發 "On-demand" 生圖。  
4. **Action (指指通)**：  
   * 點擊任一菜色進入「溝通模式」。  
   * 全螢幕顯示：超大日文菜名 \+ 羅馬拼音。  
   * 功能鍵：播放標準日語語音 (TTS)。

## **3\. 技術架構 (Technical Architecture)**

### **3.1 Client (iOS)**

* **Framework**: SwiftUI (確保原生流暢度)。  
* **Architecture**: MVVM \+ Thin Client (邏輯盡量後移)。  
* **Key Libs**: AVFoundation (相機/TTS), Kingfisher (圖片緩存)。

### **3.2 Backend (Serverless / Container)**

* **API**: Python (FastAPI/Litestar) on Cloud Run or AWS Lambda.  
* **Orchestrator**: LangChain 或自行封裝的 Agent 邏輯，負責串接 Gemini API。  
* **Response Strategy**: Streaming Response (讓前端能逐行顯示翻譯結果，降低等待焦慮)。

### **3.4 Public Domain 與路由契約 (Public Domain & Routing Contract)**

v1 對外唯一入口網域：`https://omakase.thinkwithblack.com`。

- iOS Client 只呼叫 public domain，不直接呼叫 Cloud Run 服務 URL。
- Cloudflare Worker 作為 Edge Gateway，反向代理 SSE endpoint 至 Cloud Run，並在同一網域提供 `/assets/*` 的資產存取。

### **3.3 AI Stack (The Brain)**

* **Primary Model (VLM)**: **Gemini 3 Pro Preview**  
  * **任務**：OCR、JSON 結構化、翻譯、食材分析、Image Prompt 生成。  
  * **優勢**：利用其 Thinking Process 解決「鬼畫符」等級的手寫字。  
* **Visual Model**: **Gemini 3 Pro Image Preview**  
  * **任務**：根據 Primary Model 產生的 Prompt 繪製插畫。  
  * **Style Control**: 透過 Prompt 強制鎖定 "Watercolor, Ghibli-style"。

## **4\. 關鍵 Prompt 策略 (Prompt Engineering Strategy)**

這是本產品的靈魂。我們不需要兩次 API call，而是讓 VLM 一次性吐出所有需要的資料。

**System Prompt 設計邏輯：**

Markdown  
Role: 你是精通日本料理歷史與書法的資深美食家。  
Task: 接收一張手寫菜單圖片，輸出結構化的 JSON 資料。

Process:  
1\.  \*\*OCR 與 推理\*\*：識別圖片中的文字。若字跡潦草，請根據居酒屋常見菜色與上下文(Context)進行推理修正（例如：看到「焼き」後面接不明字體，且像是雞肉部位，推測為「鳥」或特定部位）。  
2\.  \*\*翻譯與解釋\*\*：  
    \- 將菜名翻譯為台灣繁體中文（意譯）。  
    \- 提供一句話的「口感/食材描述」（針對不懂日文的食客）。  
    \- 標記是否為「推薦菜色」(Top 3)。  
3\.  \*\*Image Prompt 生成\*\*：  
    \- 針對每一道菜，生成一段用於繪圖模型的英文 Prompt。  
    \- \*\*Style Guardrail (風格圍欄)\*\*: 必須包含 "Japanese watercolor illustration, hand-drawn style, warm atmosphere, studio ghibli food style, white background"。  
    \- 描述需包含食材外觀特徵（如：Skewered chicken, golden crispy skin, green onion）。

Output Format (JSON):  
{  
  "menu\_items": \[  
    {  
      "original\_name": "親子丼",  
      "translated\_name": "炭火雞肉親子丼",  
      "description": "使用備長炭燒烤的雞腿肉，搭配半熟滑嫩的雞蛋，醬汁濃郁。",  
      "tags": \["雞肉", "主食", "推薦"\],  
      "is\_top3": true,  
      "image\_prompt": "Japanese watercolor illustration of Oyakodon, a bowl of rice topped with simmering chicken and egg, golden runny egg yolk, steam rising, wooden bowl, hand-drawn texture, warm lighting, white background."  
    },  
    ...  
  \]  
}

# **系統設計規格書 (SDD) \- Project Omakase v1.0**

## **1\. 系統架構概觀 (System Architecture)**

本系統採用 **Event-Driven Architecture (EDA)**。客戶端 (iOS) 發起請求後，與伺服器建立長連線，伺服器依序推送「文字解析結果」與「非同步生成的圖片」，確保使用者在最短時間內看到內容，隨後補齊視覺素材。

### **1.1 技術堆疊 (Tech Stack)**

* **Protocol**: HTTP/2 Server-Sent Events (SSE) (MIME type: text/event-stream)  
* **Backend**: Python (FastAPI) \- 原生支援非同步 Generator。  
* **AI Engine**:  
  * **Thinking/OCR**: Gemini 3 Pro Preview  
  * **Visual Gen**: Gemini 3 Pro Image Preview  
* **Database**:  
  * **Vector Store**: Supabase (pgvector) \- 用於嚴格圖像比對。  
  * **App DB**: PostgreSQL \- 儲存菜單結構與 Log。

## **2\. 介面規格 (Interface Specifications)**

### **2.1 核心串流 API (Core Streaming Endpoint)**

* **URL**: POST /api/v1/scan/stream  
* **Header**: Accept: text/event-stream

**Request Body**:  
JSON  
{  
  "image\_base64": "...", // 原始圖片  
  "user\_preferences": { "language": "zh-TW" }  
}

* 

**Response Stream (依序推送的 Events)**:  
事件契約以 `spec/01_API_SSE.md` 為準。以下為 v1 摘要：

 **Event Ordering (Typical)**
 1. `status` (0..n)
 2. `menu_data` (0..n)
 3. `image_update` (0..n)
 4. `done` (exactly 1)

**Event: status**
- 用於進度與 keep-alive。
- 建議 cadence：連線建立立刻送出；長步驟至少每 10–15 秒送一次。

Payload:
```json
{
  "step": "uploading" | "analyzing" | "generating_images" | "finalizing",
  "message": "string"
}
```

**Event: menu_data** *(VLM 分析完成)*
```json
{
  "session_id": "uuid",
  "items": [
    {
      "id": "string",
      "original_name": "string",
      "translated_name": "string",
      "description": "string",
      "tags": ["string"],
      "is_top3": true,
      "image_status": "pending" | "ready" | "none" | "failed",
      "image_prompt": "string"
    }
  ]
}
```

*(此時 iOS 介面已經可以顯示列表，Top 3 圖片欄位顯示 Loading spinner)*

**Event: image_update** *(Top 3 圖片生成完成，推播多次)*
```json
{
  "session_id": "uuid",
  "item_id": "string",
  "image_status": "ready" | "failed",
  "image_url": "https://omakase.thinkwithblack.com/assets/gen/<session_id>/<item_id>.jpg"
}
```

**Event: error**
```json
{
  "code": "string",
  "message": "string",
  "recoverable": true
}
```

Recommended error codes (v1):
- `IMAGE_NOT_MENU`
- `IMAGE_TOO_BLURRY`
- `VLM_FAILED`
- `IMAGE_GEN_FAILED`
- `UPSTREAM_TIMEOUT`

**Event: done**
```json
{
  "status": "completed" | "failed"
}
```

 *(optional extended `done` payload; backward-compatible, see `spec/01_API_SSE.md`)*

 ```json
 {
   "status": "completed" | "failed",
   "session_id": "uuid",
   "summary": {
     "elapsed_ms": 12345,
     "items_count": 12,
     "used_cache": true,
     "used_fallback": false,
     "unknown_items_count": 3
   }
 }
 ```

* 

## **3\. 領域邏輯與規則 (Domain Logic & Behavior)**

### **3.1 嚴格向量快取 (Strict Vector Cache)**

* **指紋計算**: 使用 Multimodal Embedding Model 計算上傳圖片的向量。  
* **嚴格閾值**: 設定 Similarity Threshold \> **0.99** (或 Euclidean Distance \< 0.02)。  
  * **Hit**: 圖片幾乎完全一樣 \-\> 直接從 DB 取出上次的 JSON 與圖片 URL，透過 SSE 秒速回傳。  
  * **Miss**: 即使只有一點點不同（如手寫修改了價格或品項），視為新菜單，進入 3.2 流程。

### **3.2 混合推薦與生圖 (Hybrid Rec & Gen)**

* **Top 3 邏輯**:  
  * VLM 分析時，System Prompt 強制要求標記 is\_top3: boolean。  
  * 優先級：視覺強調 (大字/紅字) \> 知識庫推薦 (該店類型的必吃菜)。  
* **生圖觸發**:  
  * 僅對 is\_top3 \== true 的項目，將 VLM 產生的 image\_prompt 送入 Gemini 3 Image 模型。  
  * **風格鎖定**: Prompt 自動追加 suffix: ", watercolor style, hand-drawn food illustration, warm tones, white background, studio ghibli vibes"。

### **3.3 錯誤處理 (Error Handling)**

* **VLM 拒絕/失敗**: 若圖片模糊或非菜單，Event menu\_data 回傳空列表或錯誤碼，前端顯示「請重新拍攝」。  
* **生圖失敗**: 若 Gemini Image 拒絕生成 (Safety Filter)，Event image\_update 回傳 default placeholder icon，不中斷流程。

## **4\. 資料庫設計 (Database Schema)**

**Table: menu\_scans (存取與快取核心)**

| Column | Type | Constraints | Note |
| :---- | :---- | :---- | :---- |
| id | UUID | PK |  |
| image\_hash | String | Index | 用於快速排除完全不同的圖 (SHA256) |
| embedding | Vector(768) |  | 用於 pgvector 嚴格比對 |
| raw\_json\_cache | JSONB |  | 完整儲存 VLM 解析結果 |
| created\_at | Timestamp |  |  |

**Table: generated\_assets (圖片資產)**

| Column | Type | Constraints | Note |
| :---- | :---- | :---- | :---- |
| id | UUID | PK |  |
| scan\_id | UUID | FK | 關聯到 menu\_scans |
| item\_name | String |  | 用於後續可能的文字搜尋 |
| s3\_url | String |  | 圖片 CDN 網址 |
| prompt\_used | Text |  | 紀錄當時的 Prompt 以便調校 |

## **5\. 佈署與設定 (Runtime Configuration)**

環境變數與 secret 管理由 `spec/04_SECRETS_ENV.md` 定義。本文件僅列出 v1 的高層契約：

- Public domain: `https://omakase.thinkwithblack.com`
- `PUBLIC_BASE_URL` 應設定為 public domain，以確保 SSE 回傳的 `image_url` 同網域可用。
- Secrets 不可寫入 repo；應使用 Cloud Run Secret Manager / Cloudflare secrets。

資產路徑契約（R2 key 與 URL 慣例）以 `spec/02_STORAGE_R2.md` 為準：
- Object key: `gen/<session_id>/<item_id>.jpg`
- Public URL: `https://omakase.thinkwithblack.com/assets/gen/<session_id>/<item_id>.jpg`

## **6\. Release Notes (Draft)**

Release notes 與變更摘要以 `spec/13_RELEASE_NOTES_DRAFT.md` 為準。
