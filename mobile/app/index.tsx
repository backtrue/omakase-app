import { View, Text, TouchableOpacity, Alert, StyleSheet, LayoutChangeEvent } from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as ImagePicker from "expo-image-picker";
import * as ImageManipulator from "expo-image-manipulator";
import { MaterialIcons } from "@expo/vector-icons";
import { useAppStore } from "@/lib/store";
import {
  getSignedUploadUrl,
  uploadToGCSFromUri,
  createScanJob,
  streamJobEvents,
  JobEventCallbacks,
} from "@/lib/api";
import { registerForPushNotifications } from "@/lib/notifications";
import { useEffect, useRef, useState, useCallback } from "react";

// Crop region type for image manipulation
interface CropRegion {
  originX: number;
  originY: number;
  width: number;
  height: number;
}

// Scan frame dimensions (must match styles.scanFrame)
const SCAN_FRAME_WIDTH = 280;
const SCAN_FRAME_HEIGHT = 380;

// Hidden feature flag for full menu mode (future premium feature)
const ENABLE_FULL_MENU_MODE = false;

export default function ScanScreen() {
  const router = useRouter();
  const {
    resetSession,
    setOriginalImage,
    setStatus,
    setMenuItems,
    updateItemImage,
    setError,
    setDone,
    setJobId,
    setLastEventId,
    setGcsUri,
    pushToken,
    setPushToken,
  } = useAppStore();

  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const frameRef = useRef<View>(null);
  const insets = useSafeAreaInsets();
  
  // Hidden state for full menu mode (future premium feature)
  const [fullMenuMode, setFullMenuMode] = useState(false);
  
  // Layout measurements for crop calculation
  const [cameraLayout, setCameraLayout] = useState<{ width: number; height: number } | null>(null);
  // frameAbsoluteY stores the Y position relative to screen (measured via measureInWindow)
  const [frameAbsoluteY, setFrameAbsoluteY] = useState<number | null>(null);

  useEffect(() => {
    (async () => {
      // Request camera permission if not granted
      if (!permission?.granted) {
        await requestPermission();
      }

      // Register for push notifications
      const token = await registerForPushNotifications();
      if (token) {
        setPushToken(token);
      }
    })();

    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const createEventCallbacks = (): JobEventCallbacks => ({
    onStatus: (event) => {
      const stepMap: Record<string, { status: any; msg: string }> = {
        downloading: { status: "scanning", msg: "正在下載圖片..." },
        analyzing: { status: "analyzing", msg: event.message },
        ocr: { status: "scanning", msg: "正在辨識菜單文字..." },
        translate: { status: "translating", msg: "正在翻譯菜單..." },
        generating_images: { status: "generating_images", msg: "正在生成菜品圖片..." },
      };
      const mapped = stepMap[event.step] || { status: "analyzing", msg: event.message };
      setStatus(mapped.status, mapped.msg, event.progress);
    },
    onMenuData: (event) => {
      if (__DEV__) {
        console.log("[SSE] menu_data received:", event.items.length, "items, is_partial:", event.is_partial);
      }
      setMenuItems(event.items, event.is_partial ?? false);
    },
    onImageUpdate: (event) => {
      if (__DEV__) {
        console.log("[SSE] image_update:", event.item_id, event.image_status, event.image_url);
      }
      updateItemImage(event.item_id, event.image_status, event.image_url);
    },
    onError: (event) => {
      if (event.recoverable) {
        if (__DEV__) {
          console.warn("[SSE] Recoverable error:", event.code, event.message);
        }
        return;
      }
      setError(event.code, event.message);
      Alert.alert("錯誤", event.message, [
        { text: "確定", onPress: () => router.replace("/") },
      ]);
    },
    onDone: (event) => {
      setDone(event.summary.elapsed_ms, event.summary.used_cache);
      if (event.status === "completed" || event.status === "partial") {
        router.replace("/menu");
      }
    },
    onEventId: (eventId) => {
      setLastEventId(eventId);
    },
    onHeartbeat: () => {},
  });

  // Calculate crop region based on frame position relative to camera preview
  const calculateCropRegion = useCallback(async (photoWidth: number, photoHeight: number) => {
    if (!cameraLayout || fullMenuMode) {
      return null; // No crop in full menu mode or if layout not measured
    }
    
    const screenWidth = cameraLayout.width;
    const screenHeight = cameraLayout.height;
    
    // Frame is centered horizontally
    const frameX = (screenWidth - SCAN_FRAME_WIDTH) / 2;
    
    // Use measured absolute Y position, or estimate if not available
    let frameY: number;
    if (frameAbsoluteY !== null) {
      frameY = frameAbsoluteY;
    } else {
      // Estimate: topOverlay takes roughly 20-25% of available space
      const hintHeight = 50;
      const controlsHeight = screenHeight * 0.25;
      const topOverlayHeight = (screenHeight - SCAN_FRAME_HEIGHT - hintHeight - controlsHeight);
      frameY = Math.max(0, topOverlayHeight * 0.5);
    }
    
    // Convert screen coordinates to photo coordinates
    const scaleX = photoWidth / screenWidth;
    const scaleY = photoHeight / screenHeight;
    
    const cropX = Math.max(0, Math.round(frameX * scaleX));
    const cropY = Math.max(0, Math.round(frameY * scaleY));
    const cropWidth = Math.min(photoWidth - cropX, Math.round(SCAN_FRAME_WIDTH * scaleX));
    const cropHeight = Math.min(photoHeight - cropY, Math.round(SCAN_FRAME_HEIGHT * scaleY));
    
    if (__DEV__) {
      console.log('[Crop] Screen:', screenWidth, 'x', screenHeight);
      console.log('[Crop] Photo:', photoWidth, 'x', photoHeight);
      console.log('[Crop] Frame screen pos:', frameX, frameY);
      console.log('[Crop] Crop region:', cropX, cropY, cropWidth, cropHeight);
    }
    
    return {
      originX: cropX,
      originY: cropY,
      width: cropWidth,
      height: cropHeight,
    };
  }, [cameraLayout, frameAbsoluteY, fullMenuMode]);

  const handleImageSelected = async (uri: string, cropRegion?: CropRegion | null) => {
    try {
      resetSession();
      setOriginalImage(uri);
      setStatus("scanning", "正在處理圖片...", 0);
      router.push("/analysis");

      // Step 1: Preprocess image (crop if region provided, then resize)
      const { base64, uri: processedUri } = await preprocessImageWithUri(uri, cropRegion);

      // Step 2: Get signed URL for upload
      setStatus("scanning", "正在準備上傳...", 10);
      const signedUrlResponse = await getSignedUploadUrl("image/jpeg");

      // Step 3: Upload image to GCS
      setStatus("scanning", "正在上傳圖片...", 20);
      await uploadToGCSFromUri(signedUrlResponse.upload_url, processedUri, "image/jpeg");
      setGcsUri(signedUrlResponse.gcs_uri);

      // Step 4: Create scan job (with push token for background notification)
      setStatus("scanning", "正在建立掃描任務...", 30);
      const jobResponse = await createScanJob(signedUrlResponse.gcs_uri, "繁體中文", pushToken);
      setJobId(jobResponse.job_id);

      if (__DEV__) {
        console.log("[Scan] Job created:", jobResponse.job_id);
      }

      // Step 5: Stream events from job
      setStatus("analyzing", "主廚正在解讀手寫字...", 40);
      abortRef.current = streamJobEvents(jobResponse.job_id, createEventCallbacks());

    } catch (error) {
      console.error("Scan error:", error);
      setError("SCAN_FAILED", "掃描失敗，請重試");
      Alert.alert("錯誤", "掃描失敗，請重試", [
        { text: "確定", onPress: () => router.replace("/") },
      ]);
    }
  };

  const takePhoto = async () => {
    if (!cameraRef.current) return;

    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 1,
        skipProcessing: false,
      });

      if (photo?.uri && photo.width && photo.height) {
        // Calculate crop region based on scan frame position
        const cropRegion = await calculateCropRegion(photo.width, photo.height);
        handleImageSelected(photo.uri, cropRegion);
      } else if (photo?.uri) {
        // Fallback: no dimensions available, skip crop
        handleImageSelected(photo.uri, null);
      }
    } catch (error) {
      console.error("Failed to take photo:", error);
      Alert.alert("錯誤", "拍照失敗，請重試");
    }
  };

  const pickFromLibrary = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== "granted") {
      Alert.alert("需要相簿權限", "請在設定中允許相簿存取");
      return;
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 1,
      allowsEditing: false,
    });

    if (!result.canceled && result.assets[0]) {
      // Library images are not cropped (no frame alignment)
      // In future premium mode, this could use full menu processing
      handleImageSelected(result.assets[0].uri, null);
    }
  };

  // Layout event handlers for accurate crop calculation
  // Must be defined before any conditional returns to maintain hooks order
  const onCameraLayout = useCallback((event: LayoutChangeEvent) => {
    const { width, height } = event.nativeEvent.layout;
    setCameraLayout({ width, height });
    if (__DEV__) {
      console.log('[Layout] Camera:', width, 'x', height);
    }
  }, []);

  const onFrameLayout = useCallback(() => {
    // Use measureInWindow to get absolute screen position
    if (frameRef.current) {
      frameRef.current.measureInWindow((x, y, width, height) => {
        setFrameAbsoluteY(y);
        if (__DEV__) {
          console.log('[Layout] Frame absolute:', x, y, width, height);
        }
      });
    }
  }, []);

  // Show permission request screen if not granted
  if (!permission?.granted) {
    return (
      <SafeAreaView className="flex-1 bg-black items-center justify-center">
        <MaterialIcons name="camera-alt" size={64} color="rgba(255,255,255,0.5)" />
        <Text className="text-white/70 text-lg mt-4 text-center px-8">
          需要相機權限才能掃描菜單
        </Text>
        <TouchableOpacity
          onPress={requestPermission}
          className="mt-6 px-8 py-3 bg-white rounded-full"
        >
          <Text className="text-black font-semibold">允許相機存取</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  return (
    <View style={styles.container} onLayout={onCameraLayout}>
      {/* Live Camera Preview */}
      <CameraView
        ref={cameraRef}
        style={styles.camera}
        facing="back"
      />

      {/* Overlay with scan frame - positioned absolutely on top of camera */}
      <SafeAreaView style={styles.overlay}>
        {/* Top area */}
        <View style={styles.topOverlay} />

        {/* Middle area with scan frame */}
        <View style={styles.middleRow}>
          <View style={styles.sideOverlay} />
          <View ref={frameRef} style={styles.scanFrame} onLayout={onFrameLayout}>
            <View style={styles.cornerTL} />
            <View style={styles.cornerTR} />
            <View style={styles.cornerBL} />
            <View style={styles.cornerBR} />
          </View>
          <View style={styles.sideOverlay} />
        </View>

        {/* Hint text */}
        <View style={styles.hintContainer}>
          <Text style={styles.hintText}>將菜單對準框內</Text>
        </View>

        {/* Bottom controls */}
        <View style={styles.controls}>
          <View style={styles.controlsRow}>
            {/* Gallery Button */}
            <TouchableOpacity
              onPress={pickFromLibrary}
              style={styles.galleryButton}
            >
              <MaterialIcons name="photo-library" size={28} color="white" />
            </TouchableOpacity>

            {/* Capture Button */}
            <TouchableOpacity onPress={takePhoto} style={styles.captureButton}>
              <View style={styles.captureButtonInner} />
            </TouchableOpacity>

            {/* Placeholder for symmetry */}
            <View style={styles.placeholder} />
          </View>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "black",
  },
  camera: {
    flex: 1,
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
  },
  topOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
  },
  middleRow: {
    flexDirection: "row",
  },
  sideOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
  },
  scanFrame: {
    width: 280,
    height: 380,
    position: "relative",
  },
  cornerTL: {
    position: "absolute",
    top: 0,
    left: 0,
    width: 40,
    height: 40,
    borderTopWidth: 3,
    borderLeftWidth: 3,
    borderColor: "white",
    borderTopLeftRadius: 8,
  },
  cornerTR: {
    position: "absolute",
    top: 0,
    right: 0,
    width: 40,
    height: 40,
    borderTopWidth: 3,
    borderRightWidth: 3,
    borderColor: "white",
    borderTopRightRadius: 8,
  },
  cornerBL: {
    position: "absolute",
    bottom: 0,
    left: 0,
    width: 40,
    height: 40,
    borderBottomWidth: 3,
    borderLeftWidth: 3,
    borderColor: "white",
    borderBottomLeftRadius: 8,
  },
  cornerBR: {
    position: "absolute",
    bottom: 0,
    right: 0,
    width: 40,
    height: 40,
    borderBottomWidth: 3,
    borderRightWidth: 3,
    borderColor: "white",
    borderBottomRightRadius: 8,
  },
  hintContainer: {
    backgroundColor: "rgba(0,0,0,0.5)",
    paddingVertical: 16,
    alignItems: "center",
  },
  hintText: {
    color: "rgba(255,255,255,0.8)",
    fontSize: 16,
  },
  controls: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "center",
  },
  controlsRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 48,
  },
  galleryButton: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: "rgba(255,255,255,0.15)",
    alignItems: "center",
    justifyContent: "center",
  },
  captureButton: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: "white",
    alignItems: "center",
    justifyContent: "center",
  },
  captureButtonInner: {
    width: 64,
    height: 64,
    borderRadius: 32,
    borderWidth: 4,
    borderColor: "black",
  },
  placeholder: {
    width: 56,
    height: 56,
  },
});

// Helper function that returns both base64 and the processed file URI
// Optionally crops to the specified region before resizing
async function preprocessImageWithUri(
  uri: string,
  cropRegion?: CropRegion | null
): Promise<{ base64: string; uri: string }> {
  // Build actions array: crop first (if provided), then resize
  const actions: ImageManipulator.Action[] = [];
  
  if (cropRegion && cropRegion.width > 0 && cropRegion.height > 0) {
    actions.push({ crop: cropRegion });
  }
  
  // Always resize to max 2048 width for consistent processing
  actions.push({ resize: { width: 2048 } });

  const manipulated = await ImageManipulator.manipulateAsync(
    uri,
    actions,
    {
      compress: 0.85,
      format: ImageManipulator.SaveFormat.JPEG,
      base64: true,
    }
  );

  return {
    base64: manipulated.base64 || "",
    uri: manipulated.uri,
  };
}
