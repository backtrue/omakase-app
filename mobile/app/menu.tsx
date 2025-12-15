import { View, Text, ScrollView, TouchableOpacity, Image, FlatList } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialIcons } from "@expo/vector-icons";
import { useAppStore } from "@/lib/store";
import { MenuItem } from "@/types";

function MenuItemCard({ item, onPress }: { item: MenuItem; onPress: () => void }) {
  return (
    <TouchableOpacity
      onPress={onPress}
      className="flex-row bg-white rounded-2xl p-4 mb-3 shadow-sm border border-neutral-100"
    >
      {/* Image */}
      <View className="w-20 h-20 rounded-xl bg-neutral-100 overflow-hidden mr-4">
        {item.image_url ? (
          <Image source={{ uri: item.image_url }} className="w-full h-full" resizeMode="cover" />
        ) : (
          <View className="w-full h-full items-center justify-center">
            {item.image_status === "generating" ? (
              <MaterialIcons name="hourglass-empty" size={24} color="#999" />
            ) : (
              <MaterialIcons name="restaurant" size={24} color="#ccc" />
            )}
          </View>
        )}
      </View>

      {/* Content */}
      <View className="flex-1 justify-center">
        <Text className="text-lg font-semibold text-neutral-900 mb-1" numberOfLines={1}>
          {item.translated_name || item.original_name}
        </Text>
        <Text className="text-sm text-neutral-500 mb-2" numberOfLines={1}>
          {item.original_name}
        </Text>
        {item.tags && item.tags.length > 0 && (
          <View className="flex-row flex-wrap gap-1">
            {item.tags.slice(0, 3).map((tag, i) => (
              <View key={i} className="bg-neutral-100 px-2 py-0.5 rounded-full">
                <Text className="text-xs text-neutral-600">{tag}</Text>
              </View>
            ))}
          </View>
        )}
      </View>

      {/* Arrow */}
      <View className="justify-center">
        <MaterialIcons name="chevron-right" size={24} color="#ccc" />
      </View>
    </TouchableOpacity>
  );
}

function Top3Card({ item, onPress }: { item: MenuItem; onPress: () => void }) {
  return (
    <TouchableOpacity
      onPress={onPress}
      className="w-40 mr-3 bg-white rounded-2xl overflow-hidden shadow-sm border border-neutral-100"
    >
      <View className="w-full h-28 bg-neutral-100">
        {item.image_url ? (
          <Image source={{ uri: item.image_url }} className="w-full h-full" resizeMode="cover" />
        ) : (
          <View className="w-full h-full items-center justify-center">
            <MaterialIcons name="restaurant" size={32} color="#ccc" />
          </View>
        )}
      </View>
      <View className="p-3">
        <Text className="font-semibold text-neutral-900 mb-1" numberOfLines={1}>
          {item.translated_name || item.original_name}
        </Text>
        <Text className="text-xs text-neutral-500" numberOfLines={1}>
          {item.original_name}
        </Text>
      </View>
    </TouchableOpacity>
  );
}

export default function MenuScreen() {
  const router = useRouter();
  const { session, selectDish, resetSession } = useAppStore();
  const { menuItems, originalImageUri } = session;

  const top3Items = menuItems.filter((item) => item.is_top3);
  const regularItems = menuItems.filter((item) => !item.is_top3);

  const handleItemPress = (item: MenuItem) => {
    selectDish(item);
    router.push("/dish");
  };

  const handleNewScan = () => {
    resetSession();
    router.replace("/");
  };

  return (
    <SafeAreaView className="flex-1 bg-neutral-50">
      {/* Header */}
      <View className="flex-row items-center justify-between px-4 py-3 bg-white border-b border-neutral-100">
        <TouchableOpacity onPress={handleNewScan} className="p-2">
          <MaterialIcons name="add-a-photo" size={24} color="#333" />
        </TouchableOpacity>
        <Text className="text-lg font-semibold">菜單翻譯</Text>
        <View className="w-10" />
      </View>

      <ScrollView className="flex-1" showsVerticalScrollIndicator={false}>
        {/* Original Menu Thumbnail */}
        {originalImageUri && (
          <View className="px-4 pt-4">
            <View className="flex-row items-center bg-white rounded-xl p-3 shadow-sm">
              <Image
                source={{ uri: originalImageUri }}
                className="w-16 h-16 rounded-lg"
                resizeMode="cover"
              />
              <View className="ml-3 flex-1">
                <Text className="text-sm font-medium text-neutral-900">原始菜單</Text>
                <Text className="text-xs text-neutral-500">
                  共 {menuItems.length} 道菜品
                </Text>
              </View>
            </View>
          </View>
        )}

        {/* Top 3 Recommendations */}
        {top3Items.length > 0 && (
          <View className="mt-6">
            <Text className="text-lg font-semibold px-4 mb-3">推薦菜品</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ paddingHorizontal: 16 }}
            >
              {top3Items.map((item) => (
                <Top3Card key={item.id} item={item} onPress={() => handleItemPress(item)} />
              ))}
            </ScrollView>
          </View>
        )}

        {/* All Menu Items */}
        <View className="mt-6 px-4 pb-8">
          <Text className="text-lg font-semibold mb-3">全部菜品</Text>
          {regularItems.length > 0 ? (
            regularItems.map((item) => (
              <MenuItemCard key={item.id} item={item} onPress={() => handleItemPress(item)} />
            ))
          ) : menuItems.length === 0 ? (
            <View className="items-center py-12">
              <MaterialIcons name="restaurant-menu" size={48} color="#ccc" />
              <Text className="text-neutral-400 mt-4">尚無菜品資料</Text>
            </View>
          ) : null}
          
          {/* Show all items if no top3 distinction */}
          {top3Items.length === 0 && menuItems.length > 0 && (
            menuItems.map((item) => (
              <MenuItemCard key={item.id} item={item} onPress={() => handleItemPress(item)} />
            ))
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
