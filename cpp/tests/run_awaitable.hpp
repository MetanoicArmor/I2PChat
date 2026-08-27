#pragma once

#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/io_context.hpp>
#include <exception>
#include <optional>
#include <type_traits>
#include <utility>

namespace i2pchat::testing {

namespace asio = boost::asio;

/// Run one coroutine to completion on `context`, returning its value and
/// rethrowing any exception on this thread so Catch2 can see it.
///
/// The result is captured through an optional rather than handed to
/// `use_future`, because co_spawn's error path default-constructs the result
/// type and several of the values here — an established stream owns a socket —
/// have no default state.
template <typename Awaitable>
auto run_awaitable(asio::io_context& context, Awaitable awaitable) {
    using Value = typename Awaitable::value_type;
    std::exception_ptr failure;

    if constexpr (std::is_void_v<Value>) {
        asio::co_spawn(
            context,
            [&]() -> asio::awaitable<void> {
                try {
                    co_await std::move(awaitable);
                } catch (...) {
                    failure = std::current_exception();
                }
            },
            asio::detached);
        context.run();
        context.restart();
        if (failure) {
            std::rethrow_exception(failure);
        }
    } else {
        std::optional<Value> result;
        asio::co_spawn(
            context,
            [&]() -> asio::awaitable<void> {
                try {
                    result.emplace(co_await std::move(awaitable));
                } catch (...) {
                    failure = std::current_exception();
                }
            },
            asio::detached);
        context.run();
        context.restart();
        if (failure) {
            std::rethrow_exception(failure);
        }
        if (!result.has_value()) {
            throw std::runtime_error("coroutine finished without producing a value");
        }
        return std::move(*result);
    }
}

}  // namespace i2pchat::testing
