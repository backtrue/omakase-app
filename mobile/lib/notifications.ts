import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import { Platform } from "react-native";

// Configure how notifications are handled when app is in foreground
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

/**
 * Register for push notifications and return the Expo push token.
 * Returns null if registration fails or is not supported.
 */
export async function registerForPushNotifications(): Promise<string | null> {
  // Push notifications only work on physical devices
  if (!Device.isDevice) {
    if (__DEV__) {
      console.log("[Push] Must use physical device for push notifications");
    }
    return null;
  }

  // Check existing permissions
  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let finalStatus = existingStatus;

  // Request permissions if not already granted
  if (existingStatus !== "granted") {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }

  if (finalStatus !== "granted") {
    if (__DEV__) {
      console.log("[Push] Permission not granted for push notifications");
    }
    return null;
  }

  // Get the Expo push token
  // Note: Push notifications require a development build, not Expo Go (SDK 53+)
  try {
    // Try to get token without projectId first (works in dev builds)
    const tokenData = await Notifications.getExpoPushTokenAsync();

    if (__DEV__) {
      console.log("[Push] Expo push token:", tokenData.data);
    }

    return tokenData.data;
  } catch (error) {
    // In Expo Go, this will fail - that's expected
    if (__DEV__) {
      console.log("[Push] Push notifications not available in Expo Go. Use a development build for full functionality.");
    }
    return null;
  }
}

/**
 * Add a listener for when a notification is received while app is foregrounded.
 */
export function addNotificationReceivedListener(
  callback: (notification: Notifications.Notification) => void
): Notifications.Subscription {
  return Notifications.addNotificationReceivedListener(callback);
}

/**
 * Add a listener for when user taps on a notification.
 */
export function addNotificationResponseListener(
  callback: (response: Notifications.NotificationResponse) => void
): Notifications.Subscription {
  return Notifications.addNotificationResponseReceivedListener(callback);
}

/**
 * Get the last notification response (if app was opened via notification).
 */
export async function getLastNotificationResponse(): Promise<Notifications.NotificationResponse | null> {
  return Notifications.getLastNotificationResponseAsync();
}
