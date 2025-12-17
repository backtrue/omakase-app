import {
  MenuItem,
  SSEStatusEvent,
  SSEMenuDataEvent,
  SSEImageUpdateEvent,
  SSEErrorEvent,
  SSEDoneEvent,
  SignedUrlResponse,
  CreateJobResponse,
  JobSnapshot,
  SSEHeartbeatEvent,
} from "@/types";

const API_BASE_URL = "https://omakase.thinkwithblack.com";

// -----------------------------------------------------------------------------
// Resumable Scan API
// -----------------------------------------------------------------------------

export async function getSignedUploadUrl(contentType: string = "image/jpeg"): Promise<SignedUrlResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/uploads/signed-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_type: contentType }),
  });

  if (!response.ok) {
    throw new Error(`Failed to get signed URL: ${response.status}`);
  }

  return response.json();
}

export async function uploadToGCSFromUri(
  uploadUrl: string,
  fileUri: string,
  contentType: string
): Promise<void> {
  // Read file as base64 and upload using fetch with XMLHttpRequest
  const { File } = await import("expo-file-system/next");
  
  const file = new File(fileUri);
  const base64Data = await file.base64();
  
  // Convert base64 to Uint8Array for upload
  const binaryString = atob(base64Data);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  // Use XMLHttpRequest to send binary data
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", uploadUrl, true);
    xhr.setRequestHeader("Content-Type", contentType);

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Failed to upload to GCS: ${xhr.status}`));
      }
    };

    xhr.onerror = () => {
      reject(new Error("Network error during upload"));
    };

    // Send as ArrayBuffer
    xhr.send(bytes.buffer);
  });
}

export async function createScanJob(
  gcsUri: string,
  language: string = "繁體中文",
  pushToken?: string | null
): Promise<CreateJobResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/scan/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      gcs_uri: gcsUri,
      user_preferences: { language },
      push_token: pushToken || undefined,
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create scan job: ${response.status}`);
  }

  return response.json();
}

export async function getJobSnapshot(jobId: string): Promise<JobSnapshot> {
  const response = await fetch(`${API_BASE_URL}/api/v1/scan/jobs/${jobId}`);

  if (!response.ok) {
    throw new Error(`Failed to get job snapshot: ${response.status}`);
  }

  return response.json();
}

export interface JobEventCallbacks {
  onStatus?: (event: SSEStatusEvent) => void;
  onMenuData?: (event: SSEMenuDataEvent) => void;
  onImageUpdate?: (event: SSEImageUpdateEvent) => void;
  onError?: (event: SSEErrorEvent) => void;
  onDone?: (event: SSEDoneEvent) => void;
  onHeartbeat?: (event: SSEHeartbeatEvent) => void;
  onEventId?: (eventId: string) => void;
}

// Fix UTF-8 encoding issues in React Native XMLHttpRequest
// responseText may incorrectly interpret UTF-8 bytes as Latin-1
function fixUtf8Encoding(str: string): string {
  try {
    // Check if string looks like it needs fixing (has high bytes interpreted as Latin-1)
    // UTF-8 multi-byte sequences start with bytes >= 0x80
    let needsFix = false;
    for (let i = 0; i < str.length && i < 100; i++) {
      const code = str.charCodeAt(i);
      // Latin-1 chars 0x80-0xFF that are actually UTF-8 lead bytes
      if (code >= 0xC0 && code <= 0xFF) {
        needsFix = true;
        break;
      }
    }
    
    if (!needsFix) {
      return str;
    }
    
    // Convert Latin-1 interpreted string back to UTF-8
    // Using manual UTF-8 decoding for React Native compatibility
    const bytes: number[] = [];
    for (let i = 0; i < str.length; i++) {
      bytes.push(str.charCodeAt(i) & 0xFF);
    }
    
    // Decode UTF-8 bytes to string
    let result = '';
    let i = 0;
    while (i < bytes.length) {
      const byte1 = bytes[i];
      if (byte1 < 0x80) {
        // Single byte (ASCII)
        result += String.fromCharCode(byte1);
        i++;
      } else if ((byte1 & 0xE0) === 0xC0 && i + 1 < bytes.length) {
        // Two bytes
        const byte2 = bytes[i + 1];
        if ((byte2 & 0xC0) === 0x80) {
          result += String.fromCharCode(((byte1 & 0x1F) << 6) | (byte2 & 0x3F));
          i += 2;
        } else {
          result += String.fromCharCode(byte1);
          i++;
        }
      } else if ((byte1 & 0xF0) === 0xE0 && i + 2 < bytes.length) {
        // Three bytes (most CJK characters)
        const byte2 = bytes[i + 1];
        const byte3 = bytes[i + 2];
        if ((byte2 & 0xC0) === 0x80 && (byte3 & 0xC0) === 0x80) {
          result += String.fromCharCode(((byte1 & 0x0F) << 12) | ((byte2 & 0x3F) << 6) | (byte3 & 0x3F));
          i += 3;
        } else {
          result += String.fromCharCode(byte1);
          i++;
        }
      } else if ((byte1 & 0xF8) === 0xF0 && i + 3 < bytes.length) {
        // Four bytes (emoji, rare CJK)
        const byte2 = bytes[i + 1];
        const byte3 = bytes[i + 2];
        const byte4 = bytes[i + 3];
        if ((byte2 & 0xC0) === 0x80 && (byte3 & 0xC0) === 0x80 && (byte4 & 0xC0) === 0x80) {
          const codePoint = ((byte1 & 0x07) << 18) | ((byte2 & 0x3F) << 12) | ((byte3 & 0x3F) << 6) | (byte4 & 0x3F);
          // Convert to surrogate pair for JS string
          if (codePoint > 0xFFFF) {
            const adjusted = codePoint - 0x10000;
            result += String.fromCharCode(0xD800 + (adjusted >> 10), 0xDC00 + (adjusted & 0x3FF));
          } else {
            result += String.fromCharCode(codePoint);
          }
          i += 4;
        } else {
          result += String.fromCharCode(byte1);
          i++;
        }
      } else {
        // Invalid or incomplete sequence, keep original
        result += String.fromCharCode(byte1);
        i++;
      }
    }
    
    return result;
  } catch {
    return str;
  }
}

export function streamJobEvents(
  jobId: string,
  callbacks: JobEventCallbacks,
  lastEventId?: string
): { abort: () => void } {
  const xhr = new XMLHttpRequest();
  let url = `${API_BASE_URL}/api/v1/scan/jobs/${jobId}/events`;
  if (lastEventId) {
    url += `?last_event_id=${encodeURIComponent(lastEventId)}`;
  }

  xhr.open("GET", url, true);
  xhr.setRequestHeader("Accept", "text/event-stream");
  xhr.setRequestHeader("Accept-Charset", "utf-8");

  let processedLength = 0;
  let settled = false;

  xhr.onprogress = () => {
    const fullText = xhr.responseText;
    const newData = fullText.substring(processedLength);
    
    // Find complete events (ending with double newline)
    const lastDoubleNewline = newData.lastIndexOf("\n\n");
    if (lastDoubleNewline === -1) return; // No complete events yet
    
    const completeData = newData.substring(0, lastDoubleNewline + 2);
    processedLength += completeData.length;

    // Parse SSE events - split by double newline (event separator)
    const events = completeData.split("\n\n");

    for (const eventBlock of events) {
      if (!eventBlock.trim()) continue;

      const lines = eventBlock.split("\n");
      let currentEventId = "";
      let currentEvent = "";
      let currentData = "";

      for (const line of lines) {
        if (line.startsWith("id: ")) {
          currentEventId = line.substring(4).trim();
        } else if (line.startsWith("event: ")) {
          currentEvent = line.substring(7).trim();
        } else if (line.startsWith("data: ")) {
          currentData += line.substring(6);
        } else if (line.startsWith("data:")) {
          // data: with no space after colon
          currentData += line.substring(5);
        }
      }

      if (!currentEvent || !currentData) continue;

      try {
        // Fix UTF-8 encoding before parsing JSON
        const fixedData = fixUtf8Encoding(currentData);
        const parsed = JSON.parse(fixedData);

        if (currentEventId) {
          callbacks.onEventId?.(currentEventId);
        }

        if (__DEV__) {
          const itemCount = parsed.items?.length;
          console.log("[SSE] Event:", currentEvent, "id:", currentEventId, itemCount ? `items:${itemCount}` : "");
        }

        switch (currentEvent) {
          case "status":
            callbacks.onStatus?.(parsed as SSEStatusEvent);
            break;
          case "menu_data":
            callbacks.onMenuData?.(parsed as SSEMenuDataEvent);
            break;
          case "image_update":
            callbacks.onImageUpdate?.(parsed as SSEImageUpdateEvent);
            break;
          case "error":
            callbacks.onError?.(parsed as SSEErrorEvent);
            settled = true;
            break;
          case "done":
            callbacks.onDone?.(parsed as SSEDoneEvent);
            settled = true;
            break;
          case "heartbeat":
            callbacks.onHeartbeat?.(parsed as SSEHeartbeatEvent);
            break;
          case "timeout":
            // Server timeout, client should reconnect
            break;
        }
      } catch (e) {
        if (__DEV__) {
          console.warn("[SSE] Failed to parse:", currentEvent, currentData.substring(0, 100), e);
        }
      }
    }
  };

  xhr.onerror = () => {
    if (!settled) {
      callbacks.onError?.({ code: "NETWORK_ERROR", message: "Network error", recoverable: true });
    }
  };

  xhr.ontimeout = () => {
    if (!settled) {
      callbacks.onError?.({ code: "TIMEOUT", message: "Request timeout", recoverable: true });
    }
  };

  xhr.timeout = 330000; // 5.5 minutes (slightly longer than server's 5 min poll)

  xhr.send();

  return {
    abort: () => {
      settled = true;
      xhr.abort();
    },
  };
}

// -----------------------------------------------------------------------------
// Legacy Direct Scan API (kept for backward compatibility)
// -----------------------------------------------------------------------------

export interface ScanCallbacks {
  onStatus?: (event: SSEStatusEvent) => void;
  onMenuData?: (event: SSEMenuDataEvent) => void;
  onImageUpdate?: (event: SSEImageUpdateEvent) => void;
  onError?: (event: SSEErrorEvent) => void;
  onDone?: (event: SSEDoneEvent) => void;
}

export async function startScan(
  imageBase64: string,
  language: string = "繁體中文",
  callbacks: ScanCallbacks
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}/api/v1/scan/stream`, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("Accept", "text/event-stream");

    let buffer = "";
    let settled = false;
    let sawAnyEvent = false;

    const safeResolve = () => {
      if (settled) return;
      settled = true;
      resolve();
    };

    const safeReject = (err: Error) => {
      if (settled) return;
      settled = true;
      reject(err);
    };

    xhr.onprogress = () => {
      const newData = xhr.responseText.substring(buffer.length);
      buffer = xhr.responseText;

      const lines = newData.split("\n");
      let currentEvent = "";
      let currentData = "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.substring(7).trim();
        } else if (line.startsWith("data: ")) {
          currentData = line.substring(6).trim();

          if (currentEvent && currentData) {
            try {
              const parsed = JSON.parse(currentData);
              sawAnyEvent = true;
              switch (currentEvent) {
                case "status":
                  callbacks.onStatus?.(parsed as SSEStatusEvent);
                  break;
                case "menu_data":
                  callbacks.onMenuData?.(parsed as SSEMenuDataEvent);
                  break;
                case "image_update":
                  callbacks.onImageUpdate?.(parsed as SSEImageUpdateEvent);
                  break;
                case "error":
                  callbacks.onError?.(parsed as SSEErrorEvent);
                  safeResolve();
                  xhr.abort();
                  break;
                case "done":
                  callbacks.onDone?.(parsed as SSEDoneEvent);
                  safeResolve();
                  xhr.abort();
                  break;
              }
            } catch (e) {
              console.warn("Failed to parse SSE data:", currentData, e);
            }
            currentEvent = "";
            currentData = "";
          }
        }
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        safeResolve();
      } else {
        safeReject(new Error(`HTTP ${xhr.status}: ${xhr.statusText}`));
      }
    };

    xhr.onerror = () => {
      // Streaming requests can fire onerror when the server closes the connection.
      // If we've already received events, treat it as a graceful end.
      if (sawAnyEvent) {
        safeResolve();
        return;
      }
      safeReject(new Error("Network error"));
    };

    xhr.ontimeout = () => {
      safeReject(new Error("Request timeout"));
    };

    xhr.timeout = 300000; // 5 minutes

    const body = JSON.stringify({
      image_base64: imageBase64,
      language: language,
    });

    xhr.send(body);
  });
}
