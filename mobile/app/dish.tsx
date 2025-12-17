import { View, Text, ScrollView, TouchableOpacity, Image, Alert, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialIcons } from "@expo/vector-icons";
import { useAppStore } from "@/lib/store";
import * as Speech from "expo-speech";
import { useEffect, useState } from "react";

const AI_IMAGE_BADGE_TEXT = "AI示意";
const AI_IMAGE_DISCLAIMER_TITLE = "AI 圖片提醒";
const AI_IMAGE_DISCLAIMER_MESSAGE =
  "此圖片由 AI 生成，僅供示意參考，可能與實際上菜內容、擺盤或配料不同；請以店家實際提供為準。";

function isAiGeneratedImageUrl(url: string | undefined | null): boolean {
  if (!url) return false;
  return url.includes("/assets/gen/");
}

function showAiImageDisclaimer() {
  Alert.alert(AI_IMAGE_DISCLAIMER_TITLE, AI_IMAGE_DISCLAIMER_MESSAGE, [{ text: "了解" }]);
}

export default function DishScreen() {
  const router = useRouter();
  const { selectedDish } = useAppStore();
  const [isSpeaking, setIsSpeaking] = useState(false);

  useEffect(() => {
    return () => {
      Speech.stop();
    };
  }, []);

  if (!selectedDish) {
    return (
      <SafeAreaView className="flex-1 bg-white items-center justify-center">
        <Text className="text-neutral-400">找不到菜品資訊</Text>
        <TouchableOpacity onPress={() => router.back()} className="mt-4 p-3">
          <Text className="text-blue-500">返回</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  const showAiBadge = isAiGeneratedImageUrl(selectedDish.image_url);

  const handleSpeak = async () => {
    if (isSpeaking) {
      await Speech.stop();
      setIsSpeaking(false);
      return;
    }

    try {
      const textToSpeak = (selectedDish.reading || "").trim() || selectedDish.original_name;
      if (__DEV__) {
        console.log("[TTS] Speaking:", textToSpeak);
      }
      
      // Check available voices
      const voices = await Speech.getAvailableVoicesAsync();
      const japaneseVoice = voices.find(v => v.language.startsWith("ja"));
      if (__DEV__) {
        console.log("[TTS] Japanese voice found:", japaneseVoice?.identifier);
      }

      setIsSpeaking(true);
      
      await Speech.speak(textToSpeak, {
        language: "ja-JP",
        voice: japaneseVoice?.identifier,
        rate: 0.75,
        pitch: 1.0,
        volume: 1.0,
        onStart: () => {
          if (__DEV__) {
            console.log("[TTS] Started");
          }
        },
        onDone: () => {
          if (__DEV__) {
            console.log("[TTS] Done");
          }
          setIsSpeaking(false);
        },
        onError: (error) => {
          if (__DEV__) {
            console.log("[TTS] Error:", error);
          }
          setIsSpeaking(false);
        },
      });
    } catch (error) {
      if (__DEV__) {
        console.error("[TTS] Exception:", error);
      }
      setIsSpeaking(false);
    }
  };

  return (
    <SafeAreaView className="flex-1 bg-white">
      {/* Header */}
      <View className="flex-row items-center justify-between px-4 py-3 border-b border-neutral-100">
        <TouchableOpacity onPress={() => router.back()} className="p-2">
          <MaterialIcons name="close" size={24} color="#333" />
        </TouchableOpacity>
        <Text className="text-lg font-semibold">菜品詳情</Text>
        <View className="w-10" />
      </View>

      <ScrollView className="flex-1" showsVerticalScrollIndicator={false}>
        {/* Hero Image */}
        <View className="w-full aspect-square bg-neutral-100">
          {selectedDish.image_url ? (
            <View className="w-full h-full">
              <Image
                source={{ uri: selectedDish.image_url }}
                className="w-full h-full"
                resizeMode="cover"
              />
              {showAiBadge && (
                <Pressable
                  onPress={() => {
                    showAiImageDisclaimer();
                  }}
                  style={{
                    position: "absolute",
                    right: 12,
                    bottom: 12,
                    backgroundColor: "rgba(0,0,0,0.65)",
                    paddingHorizontal: 10,
                    paddingVertical: 6,
                    borderRadius: 12,
                  }}
                >
                  <Text style={{ color: "white", fontSize: 12, fontWeight: "700" }}>{AI_IMAGE_BADGE_TEXT}</Text>
                </Pressable>
              )}
            </View>
          ) : (
            <View className="w-full h-full items-center justify-center">
              <MaterialIcons name="restaurant" size={64} color="#ccc" />
            </View>
          )}
        </View>

        {showAiBadge && (
          <View className="px-6 pt-3">
            <Text className="text-xs text-neutral-500">
              圖片由 AI 生成，僅為示意使用，可能會與實際上菜內容有差。
            </Text>
          </View>
        )}

        {/* Content */}
        <View className="p-6">
          {/* Japanese Name */}
          <Text className="text-3xl font-bold text-neutral-900 mb-1">
            {selectedDish.original_name}
          </Text>

          {/* Reading (Furigana) */}
          {selectedDish.reading && (
            <Text className="text-lg text-neutral-500 mb-4">
              {selectedDish.reading}
            </Text>
          )}

          {/* Translated Name */}
          <View className="bg-neutral-50 rounded-2xl p-4 mb-6">
            <Text className="text-sm text-neutral-400 mb-1">翻譯</Text>
            <Text className="text-xl font-semibold text-neutral-900">
              {selectedDish.translated_name}
            </Text>
          </View>

          {/* Description */}
          {selectedDish.description && (
            <View className="mb-6">
              <Text className="text-sm text-neutral-400 mb-2">說明</Text>
              <Text className="text-base text-neutral-700 leading-6">
                {selectedDish.description}
              </Text>
            </View>
          )}

          {/* Tags */}
          {selectedDish.tags && selectedDish.tags.length > 0 && (
            <View className="mb-6">
              <Text className="text-sm text-neutral-400 mb-2">標籤</Text>
              <View className="flex-row flex-wrap gap-2">
                {selectedDish.tags.map((tag, i) => (
                  <View key={i} className="bg-neutral-100 px-3 py-1.5 rounded-full">
                    <Text className="text-sm text-neutral-700">{tag}</Text>
                  </View>
                ))}
              </View>
            </View>
          )}
        </View>
      </ScrollView>

      {/* Audio Button */}
      <View className="px-6 pb-8 pt-4 border-t border-neutral-100">
        <TouchableOpacity
          onPress={handleSpeak}
          className={`flex-row items-center justify-center py-4 rounded-2xl ${
            isSpeaking ? "bg-neutral-200" : "bg-black"
          }`}
        >
          <MaterialIcons
            name={isSpeaking ? "stop" : "volume-up"}
            size={24}
            color={isSpeaking ? "#333" : "white"}
          />
          <Text className={`ml-2 text-lg font-semibold ${isSpeaking ? "text-neutral-700" : "text-white"}`}>
            {isSpeaking ? "停止播放" : "播放日文發音"}
          </Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}
