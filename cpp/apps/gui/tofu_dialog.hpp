#pragma once

#include <QDialog>
#include <QLabel>
#include <QPushButton>

#include "i2pchat/presentation/chat_view.hpp"
#include "i2pchat/session/trust_store.hpp"

namespace i2pchat::gui {

class TofuDialog : public QDialog {
    Q_OBJECT
public:
    TofuDialog(const presentation::TrustPromptView& view, QWidget* parent = nullptr);

    [[nodiscard]] session::TrustDecision decision() const { return decision_; }

private:
    session::TrustDecision decision_ = session::TrustDecision::Reject;
};

}  // namespace i2pchat::gui
