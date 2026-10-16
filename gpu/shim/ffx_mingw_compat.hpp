// bbport: MinGW compatibility for the AMD FidelityFX SDK 1.1.4 sources in gpu/third_party/fsr-vulkan.
// They are written against MSVC's CRT, where <stdio.h> alone brings _countof and swprintf_s and
// <math.h> puts log2 in the global namespace. MinGW has the same names, but in <stdlib.h> and
// <wchar.h>, which the sources never include; the submodule's own portability header is skipped
// on Windows. Force-included by gpu/CMakeLists.txt for the two FFX targets.
#pragma once

#if defined(__MINGW32__) || defined(__MINGW64__)

#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstdlib> // _countof
#include <cwchar>  // swprintf_s
#include <new>     // placement new in ffx_vk.cpp

#ifndef _countof
template <typename T, std::size_t N>
char (*ffx_mingw_countof_helper(T (&)[N]))[N];
#define _countof(array) (sizeof(*ffx_mingw_countof_helper(array)))
#endif

using std::floor;
using std::log2;

#endif
