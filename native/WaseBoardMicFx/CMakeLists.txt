cmake_minimum_required(VERSION 3.20)
project(WaseBoardMicFx LANGUAGES CXX RC)

if(NOT MSVC)
    message(FATAL_ERROR "WaseBoardMicFx se compile avec MSVC (Visual Studio 2022 ou Build Tools, charge de travail C++).")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# CRT statique : la DLL est chargée dans audiodg.exe, on ne veut dépendre d'aucun
# Visual C++ Redistributable sur la machine du joueur.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(WaseBoardMicFx SHARED
    src/DllMain.cpp
    src/WaseBoardMicFx.cpp
    src/WaseBoardMicFx.h
    src/MicFeedProtocol.h
    src/WaseBoardMicFx.def
    src/WaseBoardMicFx.rc
)

target_compile_definitions(WaseBoardMicFx PRIVATE
    UNICODE _UNICODE
    WIN32_LEAN_AND_MEAN NOMINMAX
    _WIN32_WINNT=0x0A00 WINVER=0x0A00 NTDDI_VERSION=0x0A000005
)

target_compile_options(WaseBoardMicFx PRIVATE /W4 /permissive- /guard:cf /EHsc /utf-8)

# Pas de manifeste embarqué : Microsoft le déconseille pour les APO (certaines API qu'il
# déclenche sont interdites dans un graphe audio protégé).
target_link_options(WaseBoardMicFx PRIVATE /MANIFEST:NO /GUARD:CF /DYNAMICBASE /NXCOMPAT)

target_link_libraries(WaseBoardMicFx PRIVATE ole32 advapi32 audiomediatypecrt)

# Test d'intégration : charge la DLL et joue à la fois le moteur audio et WaseBoard (voir
# tests/ApoTest.cpp). Doit être lancé élevé (objet Global\), comme sur GitHub Actions.
option(WBMF_BUILD_TESTS "Compiler le test d'intégration tests/ApoTest.cpp" ON)
if(WBMF_BUILD_TESTS)
    add_executable(ApoTest tests/ApoTest.cpp)
    target_compile_definitions(ApoTest PRIVATE
        UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX
        _WIN32_WINNT=0x0A00 WINVER=0x0A00 NTDDI_VERSION=0x0A000005)
    target_compile_options(ApoTest PRIVATE /W4 /permissive- /EHsc /utf-8)
    target_link_libraries(ApoTest PRIVATE ole32 audiomediatypecrt)
    add_dependencies(ApoTest WaseBoardMicFx)
endif()

# Dépôt dans dist/, d'où le client C# l'embarque (src/WaseBoard/WaseBoard.csproj).
add_custom_command(TARGET WaseBoardMicFx POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_SOURCE_DIR}/dist"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "$<TARGET_FILE:WaseBoardMicFx>" "${CMAKE_CURRENT_SOURCE_DIR}/dist/"
)
