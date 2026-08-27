add_executable(i2pchat-blindbox-daemon main.cpp)

target_link_libraries(i2pchat-blindbox-daemon PRIVATE
  i2pchat::core
  i2pchat_project_options
)

install(TARGETS i2pchat-blindbox-daemon RUNTIME DESTINATION bin)

# The operator-facing assets: the unit that runs the daemon, an environment file
# to copy, and the fail2ban filter that matches its audit log.
if(UNIX AND NOT APPLE)
  install(FILES packaging/i2pchat-blindbox.service
          DESTINATION lib/systemd/system)
  install(FILES packaging/fail2ban-filter.conf
          DESTINATION share/i2pchat/blindbox/fail2ban
          RENAME i2pchat-blindbox.conf)
  install(FILES packaging/fail2ban-jail.local.example
          DESTINATION share/i2pchat/blindbox/fail2ban)
endif()
install(FILES packaging/daemon.env.example
        DESTINATION share/i2pchat/blindbox)
