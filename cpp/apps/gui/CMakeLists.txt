set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC OFF)

find_package(Qt6 REQUIRED COMPONENTS Widgets Network)

qt_add_executable(i2pchat-gui
  main.cpp
  chat_window.cpp
  profile_select_dialog.cpp
  actions_popup.cpp
  chat_model.cpp
  chat_item_delegate.cpp
  contact_list_model.cpp
  contact_item_delegate.cpp
  tofu_dialog.cpp
  group_topology_map.cpp
  router_settings_dialog.cpp
  dialog_theme.cpp
  emoji_picker.cpp
  resources.qrc
  ${CMAKE_CURRENT_SOURCE_DIR}/../tui/options.cpp
)

target_include_directories(i2pchat-gui PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../tui
)
target_compile_definitions(i2pchat-gui PRIVATE
  I2PCHAT_VERSION="${PROJECT_VERSION}"
)
target_link_libraries(i2pchat-gui PRIVATE
  i2pchat::core
  i2pchat_project_options
  Qt6::Widgets
  Qt6::Network
)

if(APPLE)
  # Homebrew Qt uses @rpath. Point the build tree at Qt's lib dir so the
  # binary runs before bundling; after install, look next to the .app.
  get_filename_component(_i2pchat_qt_lib "${Qt6_DIR}/../.." ABSOLUTE)
  set_target_properties(i2pchat-gui PROPERTIES
    BUILD_RPATH "${_i2pchat_qt_lib}"
    INSTALL_RPATH "@executable_path/../Frameworks"
    BUILD_WITH_INSTALL_RPATH OFF
  )
endif()

install(TARGETS i2pchat-gui RUNTIME DESTINATION bin)
