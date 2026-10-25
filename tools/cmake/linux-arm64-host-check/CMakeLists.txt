cmake_minimum_required(VERSION 3.25)
project(forza_linux_arm64_host_check LANGUAGES CXX)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64|ARM64)$")
    message(FATAL_ERROR "This diagnostic compile check requires native Linux ARM64")
endif()
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(FORZA_TITLE_UPDATE 0 CACHE STRING "Game version: 0 or 4")
get_filename_component(FORZA_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
find_package(rexglue 0.10.0 REQUIRED CONFIG)
# Compile the actual host integration. No generated game code or fake entrypoint.
file(GLOB_RECURSE HOST_SOURCES CONFIGURE_DEPENDS "${FORZA_ROOT}/forza-recomp/src/*.cpp")
list(FILTER HOST_SOURCES EXCLUDE REGEX "/main\\.cpp$")
add_library(forza_host_check OBJECT ${HOST_SOURCES} ../macos-host-check/app_header_check.cpp)
target_include_directories(forza_host_check PRIVATE
    "${FORZA_ROOT}/forza-recomp" "${FORZA_ROOT}/forza-recomp/src"
    "${FORZA_ROOT}/forza-recomp/thirdparty/discord-rpc/include"
    "${FORZA_ROOT}/forza-recomp/thirdparty/discord-rpc/src")
target_link_libraries(forza_host_check PRIVATE rex::runtime rex::renderdoc)
target_compile_definitions(forza_host_check PRIVATE FORZA_TITLE_UPDATE=${FORZA_TITLE_UPDATE})
set_source_files_properties("${FORZA_ROOT}/forza-recomp/src/platform/vulkan_hooks.cpp" PROPERTIES
    INCLUDE_DIRECTORIES "${FORZA_ROOT}/forza-recomp/src/platform/wsi_stubs")
