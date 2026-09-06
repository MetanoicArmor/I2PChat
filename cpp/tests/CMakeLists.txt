find_package(Catch2 3 REQUIRED)

add_library(i2pchat_test_support INTERFACE)
target_include_directories(i2pchat_test_support INTERFACE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(i2pchat_test_support INTERFACE
  I2PCHAT_VECTORS_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../testdata/vectors"
  # Lets a test read the assets that get packaged, so a shipped fail2ban regex
  # cannot drift away from the log lines it is supposed to match.
  I2PCHAT_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/.."
)

add_executable(i2pchat_tests
  test_encoding.cpp
  test_crypto_vectors.cpp
  test_protocol_frames.cpp
  test_canonical_json.cpp
  test_text_chunking.cpp
  test_handshake.cpp
  test_chat_service.cpp
  test_peer_link.cpp
  test_replica_settings.cpp
  test_peer_session.cpp
  test_session_manager.cpp
  test_secure_channel.cpp
  test_trust_store.cpp
  test_storage_sealed.cpp
  test_storage_stores.cpp
  test_storage_profile_dat.cpp
  test_profile_backup.cpp
  test_release_index.cpp
  test_signals.cpp
  test_transfer.cpp
  test_blindbox.cpp
  test_blindbox_state.cpp
  test_blindbox_coordinator.cpp
  test_blindbox_replica_client.cpp
  test_blindbox_replica_server.cpp
  test_groups.cpp
  test_groups_store.cpp
  test_groups_coordinator.cpp
  test_presentation_commands.cpp
  test_presentation_chat_view.cpp
  test_tui_options.cpp
  test_sam_protocol.cpp
  test_sam_client.cpp
  test_router_i2pd.cpp
)

target_include_directories(i2pchat_tests PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../apps/tui
)
target_sources(i2pchat_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../apps/tui/options.cpp)

target_link_libraries(i2pchat_tests PRIVATE
  i2pchat::core
  i2pchat_test_support
  Catch2::Catch2WithMain
)

include(Catch)
catch_discover_tests(i2pchat_tests)
