# Included only by the private SDK checkout, via -DREX_ASAHI_LAB_SOURCE=...
target_sources(rexgpu-xenos PRIVATE "${CMAKE_CURRENT_LIST_DIR}/backend.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/workloads.cpp" "${CMAKE_CURRENT_LIST_DIR}/raster.cpp")
target_compile_definitions(rexgpu-xenos PRIVATE REX_ASAHI_SDK_LAB=1)
if(REX_ASAHI_SUBMISSION_PROFILE)
    # Requires the optional source overlay; never selected by the baseline builder.
    target_include_directories(rexgpu-xenos PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
    target_compile_definitions(rexgpu-xenos PRIVATE REX_ASAHI_SUBMISSION_PROFILE=1)
endif()
add_executable(asahi_sdk_probe "${CMAKE_CURRENT_LIST_DIR}/main.cpp")
target_link_libraries(asahi_sdk_probe PRIVATE rexruntime ${CMAKE_DL_LIBS})
add_dependencies(asahi_sdk_probe rexgpu-xenos)
add_executable(asahi_memory_alias "${CMAKE_CURRENT_LIST_DIR}/memory_alias.cpp")
target_link_libraries(asahi_memory_alias PRIVATE rexruntime)
