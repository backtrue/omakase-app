import * as ImageManipulator from "expo-image-manipulator";
import { File } from "expo-file-system/next";

const MAX_DIMENSION = 2048;
const JPEG_QUALITY = 0.85;

export async function preprocessImage(uri: string): Promise<string> {
  // Resize if needed and convert to JPEG
  const manipulated = await ImageManipulator.manipulateAsync(
    uri,
    [{ resize: { width: MAX_DIMENSION } }],
    {
      compress: JPEG_QUALITY,
      format: ImageManipulator.SaveFormat.JPEG,
      base64: true,
    }
  );

  // Return base64 directly from manipulator if available
  if (manipulated.base64) {
    return manipulated.base64;
  }

  // Fallback: read file as base64 using new API
  const file = new File(manipulated.uri);
  const base64 = await file.base64();
  return base64;
}
