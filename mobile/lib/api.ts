import {
  MenuItem,
  SSEStatusEvent,
  SSEMenuDataEvent,
  SSEImageUpdateEvent,
  SSEErrorEvent,
  SSEDoneEvent,
} from "@/types";

const API_BASE_URL = "https://omakase.thinkwithblack.com";

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
