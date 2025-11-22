import matplotlib.pyplot as plt
import tensorflow as tf
import numpy as np
import random


class WindowPredictor:
    def __init__(
        self,
        model,
        input_width,
        label_width,
        shift,
        column_indices,
        label_columns,
    ):

        self.model = model

        # Work out the label column indices
        self.label_columns = label_columns
        if label_columns is not None:
            self.label_columns_indices = {
                name: i for i, name in enumerate(label_columns)
            }
        self.column_indices = column_indices

        # Work out the window parameters
        self.input_width = input_width
        self.label_width = label_width
        self.shift = shift

        self.total_window_size = input_width + shift

        self.input_slice = slice(0, input_width)
        self.input_indices = np.arange(self.total_window_size)[self.input_slice]

        self.label_start = self.total_window_size - self.label_width
        self.labels_slice = slice(self.label_start, None)
        self.label_indices = np.arange(self.total_window_size)[self.labels_slice]

    def __repr__(self):
        return "\n\t".join(
            [
                f"Total window size: {self.total_window_size}",
                f"Input indices: {self.input_indices}",
                f"Label indices: {self.label_indices}",
                f"Label column name(s): {self.label_columns}",
            ]
        )

    def predict(self, data: tf.data.Dataset) -> np.array:
        """Make a prediction based on the given data."""
        return self.model.predict(data.batch(self.input_width)).reshape(
            (-1, len(self.label_columns))
        )

    def plot(
        self,
        data: tf.data.Dataset,
        predictions,
        plot_cols,
        title="",
        max_subplots=3,
    ) -> np.array:
        predictions = np.expand_dims(predictions, axis=0)

        inputs, labels = [], []

        for i, l in iter(data):
            inputs.append(i)
            labels.append(l)

        # Reshaping to fit into existing code
        inputs = tf.convert_to_tensor(np.array(inputs))
        labels = tf.convert_to_tensor(np.array(labels))

        # print("Inputs", inputs)
        # print("Labels", labels)

        max_n = min(max_subplots, len(inputs))

        fig = plt.figure(figsize=(12, 8))

        colors = []
        for col in plot_cols:
            colors += ["#%06X" % random.randrange(0, 2**24)]

        for n in range(max_n):
            plt.subplot(3, 1, n + 1)
            if n == 0:
                plt.title(title)
            if n == max_n // 2:
                plt.ylabel(f"{plot_cols} [normed]")
            for i, col in enumerate(plot_cols):
                plt.plot(
                    self.input_indices,
                    inputs[n, :, self.column_indices[col]],
                    label=f"{col.upper()} · inputs",
                    marker=".",
                    zorder=-10,
                    c=colors[i],
                )

                if self.label_columns:
                    label_col_index = self.label_columns_indices.get(col, None)
                else:
                    label_col_index = self.column_indices[col]

                if label_col_index is None:
                    continue

                plt.scatter(
                    self.label_indices,
                    predictions[n, :, label_col_index],
                    marker="X",
                    edgecolors="k",
                    label=f"{col.upper()} · predictions",
                    c=colors[i],
                    s=64,
                )

            if n == 0:
                plt.legend()
            elif n == max_n - 1:
                plt.xlabel("Time [h]")

        fig.tight_layout()
        return predictions.reshape((-1, len(self.label_columns)))
