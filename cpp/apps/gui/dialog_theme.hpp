#pragma once

#include <QString>

class QDialogButtonBox;
class QFrame;
class QSpinBox;
class QVBoxLayout;
class QWidget;

namespace i2pchat::gui {

void apply_dialog_theme(QWidget* widget);
void apply_dialog_theme(QWidget* widget, bool night);
void add_centered_dialog_buttons(QVBoxLayout* layout, QDialogButtonBox* buttons);
QFrame* wrap_history_numeric_row(QSpinBox* spin);
QWidget* history_field_label_block(const QString& title, const QString& hint, QWidget* parent);

}  // namespace i2pchat::gui
