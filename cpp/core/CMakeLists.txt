add_library(i2pchat_core
  src/encoding.cpp
  src/net/line_reader.cpp
  src/crypto.cpp
  src/canonical_json.cpp
  src/protocol/codec.cpp
  src/protocol/text_chunking.cpp
  src/protocol/secure_frame.cpp
  src/protocol/signals.cpp
  src/transfer/policy.cpp
  src/transfer/files.cpp
  src/transfer/manager.cpp
  src/session/handshake.cpp
  src/session/peer_session.cpp
  src/session/secure_channel.cpp
  src/session/outbound_policy.cpp
  src/session/manager.cpp
  src/session/trust_store.cpp
  src/blindbox/key_schedule.cpp
  src/blindbox/blob.cpp
  src/blindbox/replica_client.cpp
  src/blindbox/replica_server.cpp
  src/blindbox/state.cpp
  src/blindbox/coordinator.cpp
  src/groups/models.cpp
  src/groups/wire.cpp
  src/groups/invite.cpp
  src/groups/store.cpp
  src/groups/coordinator.cpp
  src/presentation/commands.cpp
  src/presentation/chat_view.cpp
  src/storage/atomic_write.cpp
  src/storage/replica_settings.cpp
  src/storage/sealed_json.cpp
  src/storage/contacts.cpp
  src/storage/compose_drafts.cpp
  src/storage/chat_history.cpp
  src/storage/group_record.cpp
  src/storage/profile_paths.cpp
  src/storage/profile_dat.cpp
  src/storage/keyring.cpp
  src/storage/keyring_macos.cpp
  src/storage/keyring_linux.cpp
  src/storage/keyring_windows.cpp
  src/runtime/chat_service.cpp
  src/runtime/identity.cpp
  src/runtime/peer_link.cpp
  src/sam/destination.cpp
  src/sam/protocol.cpp
  src/sam/client.cpp
  src/router/i2pd.cpp
)

target_include_directories(i2pchat_core
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(i2pchat_core
  PUBLIC
    nlohmann_json::nlohmann_json
    Boost::headers
  PRIVATE
    i2pchat::sodium
    i2pchat_project_options
)

# Asio needs threads and, on some platforms, an explicit standalone switch.
find_package(Threads REQUIRED)
target_link_libraries(i2pchat_core PUBLIC Threads::Threads)
target_compile_definitions(i2pchat_core PUBLIC BOOST_ASIO_NO_DEPRECATED)

if(APPLE)
  target_link_libraries(i2pchat_core PRIVATE
    "-framework CoreFoundation" "-framework Security")
elseif(WIN32)
  # Credential Manager, for the wrap-key store.
  target_link_libraries(i2pchat_core PRIVATE advapi32)
elseif(UNIX)
  # libsecret is optional: without it the .dat.wrap sidecar is the only wrap-key
  # store, which is a supported configuration rather than a broken build.
  if(PkgConfig_FOUND)
    pkg_check_modules(LIBSECRET QUIET IMPORTED_TARGET libsecret-1)
  endif()
  if(TARGET PkgConfig::LIBSECRET)
    target_link_libraries(i2pchat_core PRIVATE PkgConfig::LIBSECRET)
    target_compile_definitions(i2pchat_core PRIVATE I2PCHAT_HAVE_LIBSECRET)
  else()
    message(STATUS "libsecret not found; the OS keyring will be unavailable")
  endif()
endif()

set_target_properties(i2pchat_core PROPERTIES
  VERSION ${PROJECT_VERSION}
  POSITION_INDEPENDENT_CODE ON
)

add_library(i2pchat::core ALIAS i2pchat_core)
