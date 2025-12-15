import { View, Text, TouchableOpacity, Image, Alert } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import * as ImagePicker from "expo-image-picker";
import { MaterialIcons } from "@expo/vector-icons";
import { useAppStore } from "@/lib/store";
import { preprocessImage } from "@/lib/imageUtils";
import { startScan } from "@/lib/api";
import { useEffect, useState } from "react";

export default function ScanScreen() {
  const router = useRouter();
  const { resetSession, setOriginalImage, setStatus, setMenuItems, updateItemImage, setError, setDone } = useAppStore();
  const [cameraPermission, setCameraPermission] = useState<boolean | null>(null);
  const [libraryPermission, setLibraryPermission] = useState<boolean | null>(null);

  useEffect(() => {
    (async () => {
      const cameraResult = await ImagePicker.requestCameraPermissionsAsync();
      const libraryResult = await ImagePicker.requestMediaLibraryPermissionsAsync();
      setCameraPermission(cameraResult.status === "granted");
      setLibraryPermission(libraryResult.status === "granted");
    })();
  }, []);

  const handleImageSelected = async (uri: string) => {
    try {
      resetSession();
      setOriginalImage(uri);
      setStatus("scanning", "正在處理圖片...", 0);
      router.push("/analysis");

      const base64 = await preprocessImage(uri);
      
      await startScan(base64, "繁體中文", {
        onStatus: (event) => {
          const stepMap: Record<string, { status: typeof setStatus extends (s: infer S, ...args: any[]) => any ? S : never; msg: string }> = {
            ocr: { status: "scanning", msg: "正在辨識菜單文字..." },
            translate: { status: "translating", msg: "正在翻譯菜單..." },
            image_gen: { status: "generating_images", msg: "正在生成菜品圖片..." },
          };
          const mapped = stepMap[event.step] || { status: "analyzing", msg: event.message };
          setStatus(mapped.status as any, mapped.msg, event.progress);
        },
        onMenuData: (event) => {
          if (__DEV__) {
            console.log("[SSE] menu_data received:", event.items.length, "items, is_partial:", event.is_partial);
            console.log("[SSE] First item sample:", JSON.stringify(event.items[0]));
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
      });
    } catch (error) {
      console.error("Scan error:", error);
      setError("SCAN_FAILED", "掃描失敗，請重試");
      Alert.alert("錯誤", "掃描失敗，請重試", [
        { text: "確定", onPress: () => router.replace("/") },
      ]);
    }
  };

  const takePhoto = async () => {
    if (!cameraPermission) {
      Alert.alert("需要相機權限", "請在設定中允許相機存取");
      return;
    }

    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ["images"],
      quality: 1,
      allowsEditing: false,
    });

    if (!result.canceled && result.assets[0]) {
      handleImageSelected(result.assets[0].uri);
    }
  };

  const pickFromLibrary = async () => {
    if (!libraryPermission) {
      Alert.alert("需要相簿權限", "請在設定中允許相簿存取");
      return;
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 1,
      allowsEditing: false,
    });

    if (!result.canceled && result.assets[0]) {
      handleImageSelected(result.assets[0].uri);
    }
  };

  return (
    <SafeAreaView className="flex-1 bg-black">
      <View className="flex-1 items-center justify-center">
        {/* Camera Preview Placeholder */}
        <View className="w-full aspect-[3/4] bg-neutral-900 items-center justify-center">
          <View className="w-[80%] aspect-square border-2 border-white/30 rounded-lg items-center justify-center">
            <MaterialIcons name="restaurant-menu" size={64} color="rgba(255,255,255,0.3)" />
            <Text className="text-white/50 mt-4 text-center px-8">
              將菜單放入框內{"\n"}或從相簿選取
            </Text>
          </View>
        </View>

        {/* Controls */}
        <View className="absolute bottom-0 left-0 right-0 pb-12 pt-8 bg-black">
          <View className="flex-row items-center justify-center gap-12">
            {/* Gallery Button */}
            <TouchableOpacity
              onPress={pickFromLibrary}
              className="w-14 h-14 rounded-full bg-white/10 items-center justify-center"
            >
              <MaterialIcons name="photo-library" size={28} color="white" />
            </TouchableOpacity>

            {/* Capture Button */}
            <TouchableOpacity
              onPress={takePhoto}
              className="w-20 h-20 rounded-full bg-white items-center justify-center"
            >
              <View className="w-16 h-16 rounded-full border-4 border-black" />
            </TouchableOpacity>

            {/* Placeholder for symmetry */}
            <View className="w-14 h-14" />
          </View>
        </View>
      </View>
    </SafeAreaView>
  );
}
