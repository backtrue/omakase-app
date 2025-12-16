import { create } from "zustand";
import { MenuItem, ScanSession } from "@/types";

interface AppState {
  session: ScanSession;
  selectedDish: MenuItem | null;

  // Resumable scan state
  jobId: string | null;
  lastEventId: string | null;
  gcsUri: string | null;

  // Actions
  resetSession: () => void;
  setOriginalImage: (uri: string) => void;
  setStatus: (status: ScanSession["status"], message?: string, progress?: number) => void;
  setMenuItems: (items: MenuItem[], isPartial: boolean) => void;
  updateItemImage: (itemId: string, imageStatus: MenuItem["image_status"], imageUrl?: string) => void;
  setError: (code: string, message: string) => void;
  setDone: (elapsedMs: number, usedCache: boolean) => void;
  selectDish: (dish: MenuItem | null) => void;

  // Resumable scan actions
  setJobId: (jobId: string) => void;
  setLastEventId: (eventId: string) => void;
  setGcsUri: (gcsUri: string) => void;
  clearJob: () => void;
}

const initialSession: ScanSession = {
  sessionId: "",
  status: "idle",
  statusMessage: "",
  progress: 0,
  menuItems: [],
  originalImageUri: "",
};

export const useAppStore = create<AppState>((set) => ({
  session: { ...initialSession },
  selectedDish: null,
  jobId: null,
  lastEventId: null,
  gcsUri: null,

  resetSession: () =>
    set({
      session: { ...initialSession },
      selectedDish: null,
      jobId: null,
      lastEventId: null,
      gcsUri: null,
    }),

  setOriginalImage: (uri: string) =>
    set((state) => ({
      session: { ...state.session, originalImageUri: uri },
    })),

  setStatus: (status, message, progress) =>
    set((state) => ({
      session: {
        ...state.session,
        status,
        statusMessage: message ?? state.session.statusMessage,
        progress: progress ?? state.session.progress,
      },
    })),

  setMenuItems: (items, isPartial) =>
    set((state) => {
      const existingMap = new Map(state.session.menuItems.map((m) => [m.id, m]));
      for (const item of items) {
        const existing = existingMap.get(item.id);
        if (existing) {
          // Merge: keep existing image info if new one doesn't have it
          existingMap.set(item.id, {
            ...existing,
            ...item,
            image_url: item.image_url || existing.image_url,
            image_status: item.image_status || existing.image_status,
          });
        } else {
          existingMap.set(item.id, item);
        }
      }
      return {
        session: {
          ...state.session,
          menuItems: Array.from(existingMap.values()),
        },
      };
    }),

  updateItemImage: (itemId, imageStatus, imageUrl) =>
    set((state) => ({
      session: {
        ...state.session,
        menuItems: state.session.menuItems.map((item) =>
          item.id === itemId
            ? { ...item, image_status: imageStatus, image_url: imageUrl ?? item.image_url }
            : item
        ),
      },
    })),

  setError: (code, message) =>
    set((state) => ({
      session: {
        ...state.session,
        status: "error",
        errorCode: code,
        errorMessage: message,
      },
    })),

  setDone: (elapsedMs, usedCache) =>
    set((state) => ({
      session: {
        ...state.session,
        status: "completed",
        elapsedMs,
        usedCache,
      },
    })),

  selectDish: (dish) => set({ selectedDish: dish }),

  // Resumable scan actions
  setJobId: (jobId) => set({ jobId }),
  setLastEventId: (eventId) => set({ lastEventId: eventId }),
  setGcsUri: (gcsUri) => set({ gcsUri }),
  clearJob: () => set({ jobId: null, lastEventId: null, gcsUri: null }),
}));
