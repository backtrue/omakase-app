import "../global.css";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect } from "react";
import { StatusBar } from "expo-status-bar";
 import { setAudioModeAsync } from "expo-audio";

export { ErrorBoundary } from "expo-router";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  useEffect(() => {
    SplashScreen.hideAsync();

    (async () => {
      try {
        await setAudioModeAsync({
          playsInSilentMode: true,
          interruptionMode: "mixWithOthers",
        });
      } catch (error) {
        if (__DEV__) {
          console.log("[AudioMode] Failed to set audio mode", error);
        }
      }
    })();
  }, []);

  return (
    <>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerShown: false,
          animation: "slide_from_right",
        }}
      >
        <Stack.Screen name="index" />
        <Stack.Screen name="analysis" />
        <Stack.Screen name="menu" />
        <Stack.Screen name="dish" options={{ presentation: "modal" }} />
      </Stack>
    </>
  );
}
