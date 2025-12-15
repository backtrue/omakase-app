import { View, Text, Animated } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useEffect, useRef } from "react";
import { useAppStore } from "@/lib/store";

const STEPS = [
  { key: "scanning", label: "辨識文字" },
  { key: "translating", label: "翻譯菜單" },
  { key: "generating_images", label: "生成圖片" },
];

const DID_YOU_KNOW = [
  "日本料理講究「旬」，即食材的最佳季節",
  "壽司師傅需要經過多年訓練才能獨當一面",
  "日本拉麵的湯頭熬製通常需要 8-12 小時",
  "天婦羅的麵衣要用冰水調製才會酥脆",
  "日式燒肉源自韓國烤肉，但發展出獨特風格",
];

export default function AnalysisScreen() {
  const { session } = useAppStore();
  const rotateAnim = useRef(new Animated.Value(0)).current;
  const didYouKnow = useRef(DID_YOU_KNOW[Math.floor(Math.random() * DID_YOU_KNOW.length)]).current;

  useEffect(() => {
    const animation = Animated.loop(
      Animated.timing(rotateAnim, {
        toValue: 1,
        duration: 3000,
        useNativeDriver: true,
      })
    );
    animation.start();
    return () => animation.stop();
  }, [rotateAnim]);

  const spin = rotateAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ["0deg", "360deg"],
  });

  const currentStepIndex = STEPS.findIndex((s) => s.key === session.status);
  const progress = session.progress || (currentStepIndex >= 0 ? ((currentStepIndex + 1) / STEPS.length) * 100 : 10);

  return (
    <SafeAreaView className="flex-1 bg-white">
      <View className="flex-1 items-center justify-center px-8">
        {/* Enso Circle Animation */}
        <Animated.View
          style={{ transform: [{ rotate: spin }] }}
          className="w-32 h-32 rounded-full border-4 border-neutral-200 mb-12"
        >
          <View className="absolute top-0 left-1/2 -ml-2 w-4 h-4 rounded-full bg-black" />
        </Animated.View>

        {/* Status Message */}
        <Text className="text-2xl font-semibold text-neutral-900 mb-2 text-center">
          {session.statusMessage || "分析中..."}
        </Text>
        <Text className="text-neutral-500 mb-8 text-center">
          請稍候，正在為您處理菜單
        </Text>

        {/* Progress Bar */}
        <View className="w-full h-2 bg-neutral-100 rounded-full overflow-hidden mb-8">
          <View
            className="h-full bg-black rounded-full"
            style={{ width: `${progress}%` }}
          />
        </View>

        {/* Step Indicators */}
        <View className="flex-row justify-between w-full mb-12">
          {STEPS.map((step, index) => {
            const isActive = index <= currentStepIndex;
            const isCurrent = step.key === session.status;
            return (
              <View key={step.key} className="items-center flex-1">
                <View
                  className={`w-8 h-8 rounded-full items-center justify-center mb-2 ${
                    isActive ? "bg-black" : "bg-neutral-200"
                  }`}
                >
                  <Text className={isActive ? "text-white font-bold" : "text-neutral-400"}>
                    {index + 1}
                  </Text>
                </View>
                <Text
                  className={`text-xs text-center ${
                    isCurrent ? "text-black font-semibold" : "text-neutral-400"
                  }`}
                >
                  {step.label}
                </Text>
              </View>
            );
          })}
        </View>

        {/* Did You Know */}
        <View className="bg-neutral-50 rounded-2xl p-6 w-full">
          <Text className="text-xs text-neutral-400 mb-2">你知道嗎？</Text>
          <Text className="text-neutral-700 leading-6">{didYouKnow}</Text>
        </View>
      </View>
    </SafeAreaView>
  );
}
