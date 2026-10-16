// SPDX-FileCopyrightText: Copyright 2020 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdlib>
#include <limits>
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_semaphore.h"

#include "common/assert.h"
#include "common/logging/log.h"
#include "video_core/renderer_vulkan/vk_breadcrumbs.h"

namespace Vulkan {

u64 GpuWaitLimitSeconds() {
    static const u64 limit = [] {
        const char* env = std::getenv("BB_GPU_WAIT_LIMIT");
        if (env == nullptr || *env == '\0') {
            return u64{30};
        }
        return static_cast<u64>(std::strtoull(env, nullptr, 10));
    }();
    return limit;
}

Semaphore::Semaphore(const Instance& instance_) : instance{instance_} {
    const vk::StructureChain semaphore_chain = {
        vk::SemaphoreCreateInfo{},
        vk::SemaphoreTypeCreateInfo{
            .semaphoreType = vk::SemaphoreType::eTimeline,
            .initialValue = 0,
        },
    };
    auto [semaphore_result, sem] =
        instance.GetDevice().createSemaphoreUnique(semaphore_chain.get());
    ASSERT_MSG(semaphore_result == vk::Result::eSuccess, "Failed to create master semaphore: {}",
               vk::to_string(semaphore_result));
    semaphore = std::move(sem);
}

Semaphore::~Semaphore() = default;

void Semaphore::Refresh() {
    u64 this_tick{};
    u64 counter{};
    do {
        this_tick = gpu_tick.load(std::memory_order_acquire);
        auto [counter_result, cntr] = instance.GetDevice().getSemaphoreCounterValue(*semaphore);
        ASSERT_MSG(counter_result == vk::Result::eSuccess,
                   "Failed to get master semaphore value: {}", vk::to_string(counter_result));
        counter = cntr;
        if (counter < this_tick) {
            return;
        }
    } while (!gpu_tick.compare_exchange_weak(this_tick, counter, std::memory_order_release,
                                             std::memory_order_relaxed));
}

void Semaphore::Wait(u64 tick) {
    // No need to wait if the GPU is ahead of the tick
    if (IsFree(tick)) {
        return;
    }
    // Update the GPU tick and try again
    Refresh();
    if (IsFree(tick)) {
        return;
    }

    // If none of the above is hit, fallback to a regular wait
    const vk::SemaphoreWaitInfo wait_info = {
        .semaphoreCount = 1,
        .pSemaphores = &semaphore.get(),
        .pValues = &tick,
    };

    // bbport: this used to loop on every result forever. After a device loss, or a submission that
    // failed and never signalled the tick, the game then hung with no window updates and no report.
    u64 waited_s = 0;
    while (true) {
        const vk::Result result = instance.GetDevice().waitSemaphores(&wait_info, GPU_WAIT_SLICE_NS);
        if (result == vk::Result::eSuccess) {
            break;
        }
        ASSERT_MSG(result == vk::Result::eTimeout, "Waiting for the GPU failed: {} (tick {}, GPU at {})",
                   vk::to_string(result), tick, KnownGpuTick());
        ++waited_s;
        if (waited_s == GPU_WAIT_WARN_S) {
            LOG_WARNING(Render_Vulkan, "GPU has not reached tick {} after {} s (GPU at {})", tick,
                        waited_s, KnownGpuTick());
            Breadcrumbs::ReportStuck("wait"); // the command the GPU is stuck in, if known
        }
        const u64 limit = GpuWaitLimitSeconds();
        ASSERT_MSG(limit == 0 || waited_s < limit,
                   "GPU hang: tick {} not reached after {} s (GPU at {}); BB_GPU_WAIT_LIMIT changes "
                   "the limit",
                   tick, waited_s, KnownGpuTick());
    }
    Refresh();
}

} // namespace Vulkan
