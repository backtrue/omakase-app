export interface MenuItem {
  id: string;
  original_name: string;
  translated_name: string;
  description: string;
  tags: string[];
  is_top3: boolean;
  image_url?: string;
  image_status: "pending" | "generating" | "ready" | "none" | "failed";
  romanji?: string;
  reading?: string;  // Japanese reading in hiragana/katakana
}

export interface ScanSession {
  sessionId: string;
  status: "idle" | "scanning" | "analyzing" | "translating" | "generating_images" | "completed" | "error";
  statusMessage: string;
  progress: number;
  menuItems: MenuItem[];
  originalImageUri: string;
  elapsedMs?: number;
  usedCache?: boolean;
  errorCode?: string;
  errorMessage?: string;
}

export interface SSEStatusEvent {
  session_id: string;
  step: string;
  message: string;
  progress?: number;
}

export interface SSEMenuDataEvent {
  session_id: string;
  items: MenuItem[];
  is_partial?: boolean;
}

export interface SSEImageUpdateEvent {
  session_id: string;
  item_id: string;
  image_status: "pending" | "generating" | "ready" | "none" | "failed";
  image_url?: string;
}

export interface SSEErrorEvent {
  code: string;
  message: string;
  detail?: string;
  recoverable: boolean;
}

export interface SSEDoneEvent {
  status: "completed" | "partial" | "failed";
  session_id: string;
  summary: {
    elapsed_ms: number;
    items_count: number;
    used_cache: boolean;
    used_fallback: boolean;
    unknown_items_count: number;
  };
}

// Resumable scan API types
export interface SignedUrlResponse {
  upload_url: string;
  gcs_uri: string;
  expires_at: string;
}

export interface CreateJobResponse {
  job_id: string;
  status: string;
}

export interface JobSnapshot {
  job_id: string;
  status: string;
  items: MenuItem[];
  created_at: string;
  updated_at: string;
}

export interface SSEHeartbeatEvent {
  ts: string;
}
