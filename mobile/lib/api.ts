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

export async function uploadToGCS(uploadUrl: string, imageBlob: Blob, contentType: string): Promise<void> {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": contentType },
    body: imageBlob,
  });

  if (!response.ok) {
    throw new Error(`Failed to upload to GCS: ${response.status}`);
  }
}

export async function createScanJob(gcsUri: string, language: string = "繁體中文"): Promise<CreateJobResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/scan/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      gcs_uri: gcsUri,
      user_preferences: { language },
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

  let buffer = "";
  let settled = false;

  xhr.onprogress = () => {
    const newData = xhr.responseText.substring(buffer.length);
    buffer = xhr.responseText;

    const lines = newData.split("\n");
    let currentEventId = "";
    let currentEvent = "";
    let currentData = "";

    for (const line of lines) {
      if (line.startsWith("id: ")) {
        currentEventId = line.substring(4).trim();
      } else if (line.startsWith("event: ")) {
        currentEvent = line.substring(7).trim();
      } else if (line.startsWith("data: ")) {
        currentData = line.substring(6).trim();

        if (currentEvent && currentData) {
          try {
            const parsed = JSON.parse(currentData);

            if (currentEventId) {
              callbacks.onEventId?.(currentEventId);
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
            console.warn("Failed to parse SSE data:", currentData, e);
          }
          currentEventId = "";
          currentEvent = "";
          currentData = "";
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
