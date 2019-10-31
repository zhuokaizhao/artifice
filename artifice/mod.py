"""Implements artifice's detection scheme from end to end.
"""

import os
from time import time
import itertools
import numpy as np
from stringcase import snakecase
import tensorflow as tf
from tensorflow import keras

from artifice.log import logger
from artifice import dat
from artifice import utils
from artifice import lay


def _get_optimizer(learning_rate):
    if tf.executing_eagerly():
        return tf.train.AdadeltaOptimizer(learning_rate)
    else:
        return keras.optimizers.Adadelta(learning_rate)


def _update_hist(a, b):
    """Concat the lists in b onto the lists in a.

    If b has elements that a does not, includes them. Behavior is undefined for
    elements that are not lists.

    :param a:
    :param b:
    :returns:
    :rtype:

    """
    c = a.copy()
    for k, v in b.items():
        if isinstance(v, list) and isinstance(c.get(k), list):
            c[k] += v
        else:
            c[k] = v
    return c


def _unbatch_outputs(outputs):
    """Essentially transpose the batch dimension to the outer dimension outputs.

    :param outputs: batched outputs of the model, like
    [[pose 0, pose 1, ....],
    [output_0 0, output_0 1, ...],
    [output_1 0, output_1 1, ...]]
    :returns: result after unbatching, like
    [[pose 0, output_0 0, output_1 0, ...],
    [pose 1, output_0 1, output_1 1, ...],
    ...]

    """
    unbatched_outputs = []
    for i in range(outputs[0].shape[0]):
        unbatched_outputs.append([output[i] for output in outputs])
    return unbatched_outputs


def crop(inputs, shape=None, size=None):
    if size is None:
        assert shape is not None, 'one of `size` or `shape` must be provided'
        size = shape[1:3]
    top_crop = int(np.floor(int(inputs.shape[1] - size[0]) / 2))
    bottom_crop = int(np.ceil(int(inputs.shape[1] - size[0]) / 2))
    left_crop = int(np.floor(int(inputs.shape[2] - size[1]) / 2))
    right_crop = int(np.ceil(int(inputs.shape[2] - size[1]) / 2))
    outputs = keras.layers.Cropping2D(cropping=((top_crop, bottom_crop),
                                                (left_crop, right_crop)),
                                        input_shape=inputs.shape)(inputs)
    return outputs


def _crop_like_conv(inputs,
                    kernel_size=[3, 3],
                    padding='valid'):
    """Crop the height, width dims of inputs as if convolved with a stride of 1.

    :param inputs:
    :param kernel_size:
    :param padding:
    :returns:
    :rtype:

    """
    assert padding in {'same', 'valid'}
    if padding == 'same':
        return inputs
    top_crop = kernel_size[0] // 2
    bottom_crop = (kernel_size[0] - 1) // 2
    left_crop = kernel_size[1] // 2
    right_crop = (kernel_size[1] - 1) // 2
    outputs = keras.layers.Cropping2D(cropping=((top_crop, bottom_crop),
                                                (left_crop, right_crop)),
                                        input_shape=inputs.shape)(inputs)
    return outputs


def conv(inputs,
         filters,
         kernel_shape=[3, 3],
         activation='relu',
         padding='valid',
         kernel_initializer='glorot_normal',
         norm=True,
         mask=None,
         batch_size=None,
         activation_name=None,
         norm_name=None,
         **kwargs):
    """Perform 3x3 convolution on the layer.

    :param inputs: input tensor
    :param filters: number of filters or kernels
    :param kernel_shape:
    :param activation: keras activation to use. Default is 'relu'
    :param padding: 'valid' or 'same'
    :param norm: whether or not to perform batch normalization on the output
    :param mask: if not None, performs a sparse convolution with mask.
    :param batch_size: needed for sparse layers. Required if mask is not None

    Other kwargs passed to the convolutional layer.

    :returns:
    :rtype:

    """
    if mask is None:
        inputs = keras.layers.Conv2D(
        filters,
        kernel_shape,
        activation=None,
        padding=padding,
        use_bias=False,
        kernel_initializer=kernel_initializer,
        **kwargs)(inputs)
    else:
        inputs = lay.SparseConv2D(
        filters,
        kernel_shape,
        batch_size=batch_size,
        activation=None,
        padding=padding,
        use_bias=False,
        kernel_initializer=kernel_initializer,
        **kwargs)([inputs, mask])
    if norm:
        inputs = keras.layers.BatchNormalization(name=norm_name)(inputs)
    if activation is not None:
        inputs = keras.layers.Activation(activation, name=activation_name)(inputs)
    return inputs


def conv_upsample(inputs,
                  filters,
                  size=2,
                  activation='relu',
                  mask=None,
                  batch_size=None,
                  **kwargs):
    """Upsample the inputs in dimensions 1,2 with a transpose convolution.

    :param inputs:
    :param filters:
    :param scale: scale by which to upsample. Can be an int or a list of 2 ints,
    specifying scale in each direction.
    :param activation: relu by default
    :param mask: if not None, use a SparseConv2DTranspose layer.
    :param batch_size:

    Additional kwargs passed to the conv transpose layer.

    :returns:
    :rtype:

    """
    size = utils.listify(size, 2)
    if mask is None:
        inputs = keras.layers.Conv2DTranspose(
        filters, size,
        strides=size,
        padding='same',
        activation=activation,
        use_bias=False,
        **kwargs)(inputs)
    else:
        inputs = lay.SparseConv2DTranspose(
        filters,
        size,
        batch_size=batch_size,
        strides=size,
        padding='same',
        activation=activation,
        use_bias=False,
        **kwargs)([inputs, mask])
    return inputs


def upsample(inputs, size=2, interpolation='nearest'):
    """Upsamples the inputs by `size`, using interpolation.

    :param inputs:
    :param scale: int or 2-list of ints to scale the inputs by.
    :returns:
    :rtype:

    """
    return keras.layers.UpSampling2D(size, interpolation=interpolation)(inputs)


class Builder(type):
    """Metaclass that calls build *after* init but before finishing
    instantiation."""
    def __call__(cls, *args, **kwargs):
        obj = type.__call__(cls, *args, **kwargs)
        obj.build()
        return obj


class ArtificeModel(metaclass=Builder):
    """A wrapper around keras models.

    If loading an existing model, this class is sufficient, since the save file
    will have the model topology and optimizer. Otherwise, a subclass should
    implement the `forward()` and `compile()` methods, which are called during
    __init__. In this case, super().__init__() should be called last in the
    subclass __init__() method.

    """

    def __init__(self, input_shape, model_dir='.', learning_rate=0.1,
                overwrite=False):
        """Describe a model using keras' functional API.

        Compiles model here, so all other instantiation should be finished.

        :param inputs: tensor or list of tensors to input into the model (such as
        layers.Input)
        :param model_dir: directory to save the model. Default is cwd.
        :param learning_rate:

        :param overwrite: prefer to create a new model rather than load an existing
        one in `model_dir`. Note that if a subclass uses overwrite=False, then the
        loaded architecture may differ from the stated architecture in the
        subclass, although the structure of the saved model names should prevent
        this.

        """
        self.input_shape = input_shape
        self.overwrite = overwrite
        self.model_dir = model_dir
        self.learning_rate = learning_rate
        self.name = snakecase(type(self).__name__).lower()
        self.model_path = os.path.join(self.model_dir, f"{self.name}.hdf5")
        self.checkpoint_path = os.path.join(self.model_dir, f"{self.name}_ckpt.hdf5")
        self.history_path = os.path.join(self.model_dir, f"{self.name}_history.json")

    def build(self):
        """Called after all subclasses have finished __init__()"""
        inputs = keras.layers.Input(self.input_shape)
        outputs = self.forward(inputs)
        self.model = keras.Model(inputs, outputs)
        self.compile()

        if not self.overwrite:
            self.load_weights()

    def __str__(self):
        output = f"{self.name}:\n"
        for layer in self.model.layers:
            output += "layer:{} -> {}:{}\n".format(layer.input_shape, layer.output_shape, layer.name)
        return output

    @property
    def layers(self):
        return self.model.layers

    def forward(self, inputs):
        raise NotImplementedError("subclasses should implement")

    def compile(self):
        raise NotImplementedError("subclasses should implement")

    @property
    def callbacks(self):
        return [keras.callbacks.ModelCheckpoint(
        self.checkpoint_path, verbose=1, save_weights_only=True)]

    def load_weights(self, checkpoint_path=None):
        """Update the model weights from the chekpoint file.

        :param checkpoint_path: checkpoint path to use. If not provided, uses the
        class name to construct a checkpoint path.

        """
        if checkpoint_path is None:
            checkpoint_path = self.checkpoint_path
        if os.path.exists(checkpoint_path):
            self.model.load_weights(checkpoint_path, by_name=True)  # todo: by_name?
            logger.info(f"loaded model weights from {checkpoint_path}")
        else:
            logger.info(f"no checkpoint at {checkpoint_path}")

    def save(self, filename=None, overwrite=True):
        if filename is None:
            filename = self.model_path
        return keras.models.save_model(self.model, filename, overwrite=overwrite,
                                        include_optimizer=False)
    # todo: would like to have this be True, but custom loss function can't be
    # found in keras library. Look into it during training. For now, we're fine
    # with just weights in the checkpoint file.

    def fit(self, art_data, hist=None, cache=False, **kwargs):
        """Thin wrapper around model.fit(). Preferred method is `train()`.

        :param art_data:
        :param hist: existing hist. If None, starts from scratch. Use train for
        loading from existing hist.
        :param cache: cache the dataset.
        :returns:
        :rtype:

        """
        kwargs['callbacks'] = kwargs.get('callbacks', []) + self.callbacks
        new_hist = self.model.fit(art_data.training_input(cache=cache),
                                steps_per_epoch=art_data.steps_per_epoch,
                                **kwargs).history
        new_hist = utils.jsonable(new_hist)
        if hist is not None:
            new_hist = _update_hist(hist, new_hist)
        utils.json_save(self.history_path, new_hist)

        return new_hist

    def train(self, art_data, initial_epoch=0, epochs=1, seconds=0,
                **kwargs):
        """Fits the model, saving it along the way, and reloads every epoch.

        :param art_data: ArtificeData set
        :param initial_epoch: epoch that training is starting from
        :param epochs: epoch number to stop at. If -1, training continues forever.
        :param seconds: seconds after which to stop reloading every epoch. If -1,
        reload is never stopped. If 0, dataset is loaded only once, at beginning.
        :returns: history dictionary

        """
        if (initial_epoch > 0
            and os.path.exists(self.history_path)
            and not self.overwrite):
            hist = utils.json_load(self.history_path)
        else:
            hist = {}

        start_time = time()
        epoch = initial_epoch

        while epoch != epochs and time() - start_time > seconds > 0:
            logger.info("reloading dataset (not cached)...")
            hist = self.fit(art_data, hist=hist, initial_epoch=epoch,
                            epochs=(epoch + 1), **kwargs)
            epoch += 1

        if epoch != epochs:
            hist = self.fit(art_data, hist=hist, initial_epoch=epoch,
                            epochs=epochs, **kwargs)

        self.save()
        return hist

    def predict(self, art_data, multiscale=False):
        """Run prediction, reassembling tiles, with the Artifice data.

        :param art_data: ArtificeData object
        :returns: iterator over predictions

        """
        raise NotImplementedError("subclasses should implement.")

    def predict_visualization(self, art_data):
        """Run prediction, reassembling tiles, with the ArtificeData.

        Intended for visualization. Implementation will depend on the model.

        :param art_data: ArtificeData object
        :returns: iterator over (image, field, prediction)

        """
        raise NotImplementedError()

    def predict_outputs(self, art_data):
        """Run prediction for single tiles images with the Artifice data.

        Returns the raw outputs, with no prediction. Depends on subclass
        implementation.

        :param art_data: ArtificeData object
        :returns: iterator over (tile, prediction, model_outputs)

        """
        raise NotImplementedError("subclasses should implement")

    def evaluate(self, art_data):
        """Run evaluation for object detection with the ArtificeData object.

        Depends on the structure of the model.

        :param art_data: ArtificeData object
        :returns: `errors, total_num_failed` error matrix and number of objects not
        detected
        :rtype: np.ndarray, int

        """
        raise NotImplementedError('subclasses should implmement')

    def uncertainty_on_batch(self, images):
        """Estimate the model's uncertainty for each image.

        :param images: a batch of images
        :returns: "uncertainty" for each image.
        :rtype:

        """
        raise NotImplementedError("uncertainty estimates not implemented")


class UNet(ArtificeModel):
    def __init__(self, *, base_shape, level_filters, num_channels, pose_dim,
                level_depth=2, dropout=0.5, **kwargs):
        """Create a U-Net model for object detection.

        Regresses a distance proxy at every level for multi-scale tracking. Model
        output consists first of the `pose_dim`-channel pose image, followed by
        multi-scale fields from smallest (lowest on the U) to largest (original
        image dimension).

        :param base_shape: the height/width of the output of the first layer in the lower
        level. This determines input and output tile shapes. Can be a tuple,
        specifying different height/width, or a single integer.
        :param level_filters: number of filters at each level (bottom to top).
        :param level_depth: number of layers per level
        :param dropout: dropout to use for concatenations
        :param num_channels: number of channels in the input
        :param pose_dim:

        """
        self.base_shape = utils.listify(base_shape, 2)
        self.level_filters = level_filters
        self.num_channels = num_channels
        self.pose_dim = pose_dim
        self.level_depth = level_depth
        self.dropout = dropout

        self.num_levels = len(self.level_filters)
        self.input_tile_shape = self.compute_input_tile_shape()
        self.output_tile_shapes = self.compute_output_tile_shapes()
        self.output_tile_shape = self.output_tile_shapes[-1]

        super().__init__(self.input_tile_shape + [self.num_channels], **kwargs)

    @staticmethod
    def compute_input_tile_shape_(base_shape, num_levels, level_depth):
        """Compute the shape of the input tiles.

        :param base_shape: shape of the output of the last layer in the
        lower level.
        :param num_levels: number of levels
        :param level_depth: layers per level (per side)
        :returns: shape of the input tiles

        """
        tile_shape = np.array(base_shape)
        tile_shape += 2 * level_depth
        for _ in range(num_levels - 1):
            tile_shape *= 2
            tile_shape += 2 * level_depth
        return list(tile_shape)

    def compute_input_tile_shape(self):
        return self.compute_input_tile_shape_(self.base_shape, self.num_levels, self.level_depth)

    @staticmethod
    def compute_output_tile_shape_(base_shape, num_levels, level_depth):
        tile_shape = np.array(base_shape)
        for _ in range(num_levels - 1):
            tile_shape *= 2
            tile_shape -= 2 * level_depth
        return list(tile_shape)

    def compute_output_tile_shape(self):
        return self.compute_output_tile_shape_(self.base_shape, self.num_levels, self.level_depth)

    @staticmethod
    def compute_output_tile_shapes_(base_shape, num_levels, level_depth):
        """Compute the shape of the output tiles at every level, bottom to top."""
        shapes = []
        tile_shape = np.array(base_shape)
        shapes.append(list(tile_shape))
        for _ in range(num_levels - 1):
            tile_shape *= 2
            tile_shape -= 2 * level_depth
            shapes.append(list(tile_shape))
        return shapes

    def compute_output_tile_shapes(self):
        return self.compute_output_tile_shapes_(self.base_shape, self.num_levels, self.level_depth)

    @staticmethod
    def compute_level_input_shapes_(base_shape, num_levels, level_depth):
        """Compute the shape of the output tiles at every level, bottom to top."""
        shapes = []
        tile_shape = np.array(base_shape)
        shapes.append(list(tile_shape + 2 * level_depth))
        for _ in range(num_levels - 1):
            tile_shape *= 2
            shapes.append(list(tile_shape))
            tile_shape -= 2 * level_depth
        return shapes

    def _fix_level_index(self, level):
        if level >= 0:
            return level
        l = self.num_levels + level
        if l < 0 or l >= self.num_levels:
            raise ValueError(f"bad level: {level}")
        return l

    def convert_point_between_levels(self, point, level, new_level):
        """Convert len-2 point in the tile-space at `level` to `new_level`.

        Level 0 is the lowest level, by convention. -1 can mean the highest
        (original resolution) level.

        :param point:
        :param level: level to which the point belongs (last layer in that level).
        :param new_level: level of the space to which the point should be converted.
        :returns:
        :rtype:

        """
        level = self._fix_level_index(level)
        new_level = self._fix_level_index(new_level)
        while level < new_level:
            point *= 2
            point += self.level_depth
            level += 1
        while level > new_level:
            point /= 2
            point -= self.level_depth
            level -= 1
        return point

    def convert_distance_between_levels(self, distance, level, new_level):
        level = self._fix_level_index(level)
        new_level = self._fix_level_index(new_level)
        return distance * 2**(new_level - level)

    @staticmethod
    def pose_loss(pose, pred):
        return tf.losses.mean_squared_error(pose[:, :, :, 1:],
                                            pred[:, :, :, 1:],
                                            weights=pose[:, :, :, :1])

    def compile(self):
        optimizer = _get_optimizer(self.learning_rate)
        self.model.compile(optimizer=optimizer, loss=[self.pose_loss]
                                + ['mse'] * self.num_levels)

    def forward(self, inputs):
        level_outputs = []
        outputs = []

        for level, filters in enumerate(reversed(self.level_filters)):
            for _ in range(self.level_depth):
                inputs = conv(inputs, filters)
            if level < self.num_levels - 1:
                level_outputs.append(inputs)
                inputs = keras.layers.MaxPool2D()(inputs)
            else:
                outputs.append(conv(inputs, 1, kernel_shape=[1, 1], activation=None,
                                    norm=False, name='output_0'))

        level_outputs = reversed(level_outputs)
        for i, filters in enumerate(self.level_filters[1:]):
            inputs = conv_upsample(inputs, filters)
            cropped = crop(next(level_outputs), inputs.shape)
            dropped = keras.layers.Dropout(rate=self.dropout)(cropped)
            inputs = keras.layers.Concatenate()([dropped, inputs])
            for _ in range(self.level_depth):
                inputs = conv(inputs, filters)

            outputs.append(conv(inputs, 1, kernel_shape=[1, 1], activation=None,
                                norm=False, name=f'output_{i+1}'))

        pose_image = conv(
            inputs,
            1 + self.pose_dim,
            kernel_shape=[1, 1],
            activation=None,
            padding='same',
            norm=False,
            name='pose')
        return [pose_image] + outputs

    def predict(self, art_data, multiscale=False):
        """Run prediction, reassembling tiles, with the Artifice data."""
        if tf.executing_eagerly():
            outputs = []
        for i, batch in enumerate(art_data.prediction_input()):
            if i % 100 == 0:
                logger.info(f"batch {i} / {art_data.steps_per_epoch}")
                outputs += _unbatch_outputs(self.model.predict_on_batch(batch))
            while len(outputs) >= art_data.num_tiles:
                prediction = art_data.analyze_outputs(outputs, multiscale=multiscale)
                yield prediction
                del outputs[:art_data.num_tiles]
        else:
            raise NotImplementedError("enable eager execution for eval (remove --patient)")
        outputs = []
        next_batch = (art_data
                    .prediction_input()
                    .make_one_shot_iterator()
                    .get_next())
        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
        for i in itertools.count():
            try:
                batch = sess.run(next_batch)
            except tf.errors.OutOfRangeError:
                return
            if i % 100 == 0:
                logger.info(f"batch {i} / {art_data.steps_per_epoch}")
                outputs += _unbatch_outputs(self.model.predict_on_batch(batch))
            while len(outputs) >= art_data.num_tiles:
                prediction = art_data.analyze_outputs(
                    outputs, multiscale=multiscale)
                yield prediction
                del outputs[:art_data.num_tiles]

    def predict_visualization(self, art_data):
        """Run prediction, reassembling tiles, with the Artifice data."""
        if tf.executing_eagerly():
            tiles = []
            dist_tiles = []
            outputs = []
            p = art_data.image_padding()
            for batch in art_data.prediction_input():
                tiles += [tile[p[0][0]:, p[1][0]:] for tile in list(batch)]
                new_outputs = _unbatch_outputs(self.model.predict_on_batch(batch))
                outputs += new_outputs
                dist_tiles += [output[-1] for output in new_outputs]
                while len(outputs) >= art_data.num_tiles:
                    image = art_data.untile(tiles[:art_data.num_tiles])
                    dist_image = art_data.untile(dist_tiles[:art_data.num_tiles])
                    prediction, pose_image = art_data.analyze_outputs(outputs)
                    yield (image, dist_image, prediction, pose_image)
                    del outputs[:art_data.num_tiles]
                    del tiles[:art_data.num_tiles]
                    del dist_tiles[:art_data.num_tiles]
        else:
            raise NotImplementedError("patient prediction")

    def predict_outputs(self, art_data):
        """Run prediction for single tiles images with the Artifice data."""
        num_tiles = art_data.num_tiles
        art_data.num_tiles = 1
        if tf.executing_eagerly():
            tiles = []
            outputs = []
            p = art_data.image_padding()
        for batch in art_data.prediction_input():
            tiles += [tile[p[0][0]:, p[1][0]:] for tile in list(batch)]
            outputs += _unbatch_outputs(self.model.predict_on_batch(batch))
            while outputs:
                tile = art_data.untile(tiles[:1])
                yield (tile, outputs[0])  # todo: del line
                del outputs[0]
                del tiles[0]
        else:
            raise NotImplementedError("patient prediction")
        art_data.num_tiles = num_tiles

    def evaluate(self, art_data, multiscale=False):
        """Runs evaluation for UNet."""
        if tf.executing_eagerly():
            tile_labels = []
            errors = []
            outputs = []
            total_num_failed = 0
            for i, (batch_tiles, batch_labels) in enumerate(
                    art_data.evaluation_input()):
                if i % 10 == 0:
                    logger.info(f"evaluating batch {i} / {art_data.steps_per_epoch}")
                    tile_labels += list(batch_labels)
                    outputs += _unbatch_outputs(self.model.predict_on_batch(batch_tiles))
                while len(outputs) >= art_data.num_tiles:
                    label = art_data.untile_points(tile_labels[:art_data.num_tiles])
                    prediction = art_data.analyze_outputs(outputs, multiscale=multiscale)
                    error, num_failed = dat.evaluate_prediction(label, prediction)
                    total_num_failed += num_failed
                    errors += error[error[:, 0] >= 0].tolist()
                    del tile_labels[:art_data.num_tiles]
                    del outputs[:art_data.num_tiles]
        else:
            raise NotImplementedError("evaluation on patient execution")
        return np.array(errors), total_num_failed

    def uncertainty_on_batch(self, images):
        """Estimate the model's uncertainty for each image."""
        batch_outputs = _unbatch_outputs(self.model.predict_on_batch(images))
        confidences = np.empty(len(batch_outputs), np.float32)
        for i, outputs in enumerate(batch_outputs):
            detections = dat.multiscale_detect_peaks(outputs[1:])
            confidences[i] = np.mean([outputs[0][x, y] for x, y in detections])
        return 1 - confidences


class SparseUNet(UNet):
  def __init__(self, *, batch_size=None, block_size=[8, 8], tol=0.5, **kwargs):
    """Create a UNet-like architecture using multi-scale tracking.

    :param batch_size: determines whether variables will be used in sparse
    layers for the scatter operation.
    :param block_size: width/height of the blocks used for sparsity, at the
    scale of the original resolution (resized at each level. These are rescaled
    at each level.
    :param 8]:
    :param tol: absolute threshold value for sbnet attention.
    :returns:
    :rtype:

    """
    super().__init__(**kwargs)
    self.batch_size = batch_size
    self.block_size = utils.listify(block_size, 2)
    self.tol = tol

  def forward(self, inputs):
    if self.batch_size is not None:
      inputs.set_shape([self.batch_size] + list(inputs.shape)[1:])

    level_outputs = []
    outputs = []
    for level, filters in enumerate(reversed(self.level_filters)):
      for _ in range(self.level_depth):
        inputs = conv(inputs, filters)
      if level < self.num_levels - 1:
        level_outputs.append(inputs)
        inputs = keras.layers.MaxPool2D()(inputs)
      else:
        mask = conv(inputs, 1, kernel_shape=[1, 1], activation=None,
                    norm=False, name='output_0')
        outputs.append(mask)

    level_outputs = reversed(level_outputs)
    for i, filters in enumerate(self.level_filters[1:]):
      inputs = conv_upsample(inputs, filters, mask=mask, tol=self.tol,
                             block_size=self.block_size,
                             batch_size=self.batch_size)
      mask = upsample(mask, size=2, interpolation='nearest')

      cropped = crop(next(level_outputs), inputs.shape)
      dropped = keras.layers.Dropout(rate=self.dropout)(cropped)
      inputs = keras.layers.Concatenate()([dropped, inputs])

      for _ in range(self.level_depth):
        inputs = conv(inputs, filters, mask=mask, tol=self.tol,
                      block_size=self.block_size,
                      batch_size=self.batch_size)
        mask = _crop_like_conv(mask)
      mask = conv(
        inputs,
        1,
        kernel_shape=[1, 1],
        activation=None,
        norm=False,
        padding='same',
        mask=mask,
        tol=self.tol,
        block_size=self.block_size,
        batch_size=self.batch_size,
        name=f'output_{i+1}')
      outputs.append(mask)

    pose_image = conv(
      inputs,
      1 + self.pose_dim,
      kernel_shape=[1, 1],
      activation=None,
      padding='same',
      norm=False,
      mask=mask,
      block_size=self.block_size,
      tol=self.tol,
      batch_size=self.batch_size,
      name='pose')

    outputs = [pose_image] + outputs
    return outputs


class BetterSparseUNet(SparseUNet):
  """An extension of the SparseUNet that foregoes repeated gather and scatter
  operations. Although it uses the same inputs and outputs as the other models,
  it only uses the distance output of the first (lowest) and last level,
  outputting empty tensors (or zeros) at the other levels, not used in the
  loss.

  Uses the block_size as the base_shape for computing what is essentially a
  more dynamic UNet, with a second tiling phase. The block stride is computed
  based on the output block size.

  """

  def __init__(self, **kwargs):
    super().__init__(**kwargs)

    if self.batch_size is None:
      # todo: make this not required?
      raise ValueError(
        f'SparseScatter layer requires the batch size.')

    self.level_input_tile_sizes = self.compute_level_input_shapes_(
      self.base_shape, self.num_levels, self.level_depth)

    # block sizes
    self.level_input_block_size = list(2 * np.array(self.block_size))
    self.level_output_block_size = list(
      np.array(self.level_input_block_size) - 2 * self.level_depth)

    # block strides
    assert self.level_depth % 2 == 0, 'must have even level_depth for strides'
    self.block_stride = list(np.array(self.block_size) - self.level_depth // 2)
    self.level_input_block_stride = list(2 * np.array(self.block_stride))
    self.level_output_block_stride = self.level_output_block_size

  @staticmethod
  def compute_level_input_block_strides_(input_block_stride,
                                         num_levels,
                                         level_depth):
    """Compute the block stride at the input to every level."""
    strides = []
    stride = np.array(input_block_stride)
    strides.append(list(stride))
    for _ in range(num_levels - 1):
      stride *= 2
      strides.append(list(stride))
    return strides

  def forward(self, inputs):
    level_outputs = []
    outputs = []
    for level, filters in enumerate(reversed(self.level_filters)):
      for _ in range(self.level_depth):
        inputs = conv(inputs, filters)
      if level < self.num_levels - 1:
        level_outputs.append(inputs)
        inputs = keras.layers.MaxPool2D()(inputs)
      else:
        mask = conv(inputs, 1, kernel_shape=[1, 1], activation=None,
                    norm=False, name='output_0')
        outputs.append(mask)

    # sparsify based on the first mask
    bin_counts, active_block_indices = lay.ReduceMask(
      block_size=self.block_size,
      block_stride=self.block_stride,
      tol=self.tol)(mask)

    level_outputs = reversed(level_outputs)
    for i, filters in enumerate(self.level_filters[1:]):
      level = i + 1
      blocks = lay.SparseGather(
        block_size=self.block_size,
        block_stride=self.block_stride)(
          [inputs, bin_counts, active_block_indices])
      blocks = conv_upsample(blocks, filters)

      level_output = next(level_outputs)
      cropped = crop(level_output, size=self.level_input_tile_sizes[level])
      dropped = keras.layers.Dropout(rate=self.dropout)(cropped)
      blocked = lay.SparseGather(
        block_size=self.level_input_block_size,
        block_stride=self.level_input_block_stride)(
          [dropped, bin_counts, active_block_indices])

      blocks = keras.layers.concatenate([blocked, blocks])

      for j in range(self.level_depth):
        blocks = conv(blocks, filters)

      if level == self.num_levels - 1:
        # make the blocks for the pose_image
        blocks = conv(
          blocks,
          1 + self.pose_dim,
          kernel_shape=[1, 1],
          activation=None,
          padding='same',
          norm=False)
        name = 'pose'
      else:
        name = None

      inputs = lay.SparseScatter(
        [self.batch_size] + self.output_tile_shapes[level] + [blocks.shape[3]],
        block_size=self.level_output_block_size,
        block_stride=self.level_output_block_stride,
        name=name)(
          [blocks, bin_counts, active_block_indices])

      # convolve the level's mask at full size, use it to gather the next level
      mask_blocks = conv(
        blocks,
        1,
        kernel_shape=[1, 1],
        activation=None,
        norm=False,
        padding='same')
      mask = lay.SparseScatter(
        [self.batch_size] + self.output_tile_shapes[level] + [1],
        block_size=self.level_output_block_size,
        block_stride=self.level_output_block_stride,
        name=f'output_{level}')(
          [mask_blocks, bin_counts, active_block_indices])
      outputs.append(mask)

    outputs = [inputs] + outputs
    return outputs


class AutoSparseUNet(BetterSparseUNet):
  """An extension of the BetterSparseUNet that doesn't use the distance proxy but
  rather a learned mask for each level.

  :param gamma: sparsity loss coefficient.

  """

  def __init__(self, *args, gamma=0.01, **kwargs):
    """AutoSparseUNet.

    """
    super().__init__(*args, **kwargs)
    self.gamma = gamma

  @staticmethod
  def compute_level_input_block_strides_(input_block_stride,
                                         num_levels,
                                         level_depth):
    """Compute the block stride at the input to every level."""
    strides = []
    stride = np.array(input_block_stride)
    strides.append(list(stride))
    for _ in range(num_levels - 1):
      stride *= 2
      strides.append(list(stride))
    return strides

  @staticmethod
  def sparsity_loss(_, mask):
    """Choice of sparsity measure: hoyer entropy."""

    # Gausian entropy: -inf at 0
    # return tf.reduce_sum(tf.log(tf.square(mask)))

    # Hoyer:
    # sqrt_N = tf.sqrt(tf.cast(tf.size(mask), tf.float32))
    # sos = tf.reduce_sum(tf.square(mask))
    # num = sqrt_N - sos / tf.sqrt(sos)
    # den = sqrt_N - 1
    # return num / den

    # l_2 / l_1
    return tf.norm(mask, ord=2) / tf.norm(mask, ord=1)

  def compile(self):
    optimizer = _get_optimizer(self.learning_rate)
    self.model.compile(
      optimizer=optimizer,
      loss=[self.pose_loss] + [self.sparsity_loss] * self.num_levels,
      loss_weights=[1.] + [1. / self.num_levels] * self.num_levels)

  # todo: resolve similarities between this and other forward functions
  def forward(self, inputs):
    level_outputs = []
    outputs = []
    for level, filters in enumerate(reversed(self.level_filters)):
      for _ in range(self.level_depth):
        inputs = conv(inputs, filters)
      if level < self.num_levels - 1:
        level_outputs.append(inputs)
        inputs = keras.layers.MaxPool2D()(inputs)
      else:
        mask = conv(inputs,
                    1,
                    kernel_shape=[1, 1],
                    activation='relu',
                    norm=False,
                    activation_name=f'output_0')
        outputs.append(mask)

    # sparsify based on the first mask
    bin_counts, active_block_indices = lay.ReduceMask(
      block_size=self.block_size,
      block_stride=self.block_stride,
      tol=self.tol)(mask)

    level_outputs = reversed(level_outputs)
    for i, filters in enumerate(self.level_filters[1:]):
      level = i + 1
      blocks = lay.SparseGather(
        block_size=self.block_size,
        block_stride=self.block_stride)(
          [inputs, bin_counts, active_block_indices])
      blocks = conv_upsample(blocks, filters)

      level_output = next(level_outputs)
      cropped = crop(level_output, size=self.level_input_tile_sizes[level])
      dropped = keras.layers.Dropout(rate=self.dropout)(cropped)
      blocked = lay.SparseGather(
        block_size=self.level_input_block_size,
        block_stride=self.level_input_block_stride)(
          [dropped, bin_counts, active_block_indices])

      blocks = keras.layers.concatenate([blocked, blocks])

      for j in range(self.level_depth):
        blocks = conv(blocks, filters)

      if level == self.num_levels - 1:
        # make the blocks for the pose_image
        blocks = conv(
          blocks,
          1 + self.pose_dim,
          kernel_shape=[1, 1],
          activation=None,
          padding='same',
          norm=False)
        name = 'pose'
      else:
        name = None

      inputs = lay.SparseScatter(
        [self.batch_size] + self.output_tile_shapes[level] + [blocks.shape[3]],
        block_size=self.level_output_block_size,
        block_stride=self.level_output_block_stride,
        name=name)(
          [blocks, bin_counts, active_block_indices])

      # convolve the level's mask at full size, use it to gather the next level
      mask_blocks = conv(
        blocks,
        1,
        kernel_shape=[1, 1],
        activation='relu',
        norm=False,
        padding='same')
      mask = lay.SparseScatter(
        [self.batch_size] + self.output_tile_shapes[level] + [1],
        block_size=self.level_output_block_size,
        block_stride=self.level_output_block_stride,
        name=f'output_{level}')(
          [mask_blocks, bin_counts, active_block_indices])
      outputs.append(mask)

      bin_couns, active_block_indices = lay.ReduceMask(
        block_size=self.block_size,
        block_stride=self.block_stride,
        tol=self.tol)(mask)

    outputs = [inputs] + outputs
    return outputs
