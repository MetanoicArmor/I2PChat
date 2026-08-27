#include "tofu_dialog.hpp"

#include <QHBoxLayout>
#include <QTextEdit>
#include <QVBoxLayout>

namespace i2pchat::gui {

TofuDialog::TofuDialog(const presentation::TrustPromptView& view, QWidget* parent)
    : QDialog(parent) {
    setWindowTitle(QString::fromStdString(view.title));
    setModal(true);
    setMinimumWidth(480);

    auto* title = new QLabel(QString::fromStdString(view.title), this);
    title->setWordWrap(true);
    QFont title_font = title->font();
    title_font.setBold(true);
    title->setFont(title_font);

    auto* body = new QLabel(QString::fromStdString(view.body), this);
    body->setWordWrap(true);

    auto* fingerprint = new QTextEdit(this);
    fingerprint->setReadOnly(true);
    fingerprint->setPlainText(QString::fromStdString("New key:\n" + view.fingerprint +
                                                     (view.previous_fingerprint.empty()
                                                          ? std::string{}
                                                          : "\n\nPinned:\n" +
                                                                view.previous_fingerprint)));
    fingerprint->setMaximumHeight(140);

    auto* accept = new QPushButton(view.dangerous ? tr("Accept new key") : tr("Pin this key"),
                                   this);
    accept->setObjectName("PrimaryButton");
    auto* reject = new QPushButton(tr("Reject"), this);

    connect(accept, &QPushButton::clicked, this, [this] {
        decision_ = session::TrustDecision::Accept;
        QDialog::accept();
    });
    connect(reject, &QPushButton::clicked, this, [this] {
        decision_ = session::TrustDecision::Reject;
        QDialog::reject();
    });

    auto* buttons = new QHBoxLayout();
    buttons->addStretch();
    buttons->addWidget(reject);
    buttons->addWidget(accept);

    auto* layout = new QVBoxLayout(this);
    layout->addWidget(title);
    layout->addWidget(body);
    layout->addWidget(fingerprint);
    layout->addLayout(buttons);
}

}  // namespace i2pchat::gui
