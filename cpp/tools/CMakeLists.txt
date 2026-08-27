foreach(tool interop_peer interop_storage)
  add_executable(${tool} ${tool}.cpp)
  target_link_libraries(${tool} PRIVATE
    i2pchat::core
    i2pchat_project_options
  )
endforeach()
