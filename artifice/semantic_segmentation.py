"""Provides semantic segmentation capability for artifice. The specific
algorithm isn't important, as long as it returns an output in familiar form.

We implement the U-Net segmentation architecture
(http://arxiv.org/abs/1505.04597), loosely inspired by implementation at:
https://github.com/tks10/segmentation_unet/

"""

import os
from shutil import rmtree
import numpy as np
import logging

import tensorflow as tf
from artifice.utils import dataset

tf.logging.set_verbosity(tf.logging.INFO)

logger = logging.getLogger('artifice')

# TODO: allow for customizing these values
batch_size = 1
  

def median_frequency_weights(annotations_one_hot, num_classes):
  """Calculate the weight tensor given one-hot annotations, using Median Frequency
  balancing.

  """
  
  counts = tf.bincount(tf.cast(annotations_one_hot, tf.int32),
                       minlength=num_classes, dtype=tf.float64)
  median = tf.contrib.distributions.percentile(
    counts, q=50., interpolation='lower')
  median += tf.contrib.distributions.percentile(
    counts, q=50., interpolation='higher')
  median /= 2.
  class_weights = median / counts
  class_weights /= tf.norm(class_weights, ord=1)
  return tf.multiply(annotations_one_hot, tf.cast(class_weights, tf.float32))


"""A model implementing semantic segmentation.
args:
* channels: number of channels in the image (grayscale by default)
* num_classes: number of classes of objects to be detected (including background)

In this context, `image` is the input to the model, and `annotation`, is the
SEMANTIC annotation (groung truth) of `image`, a [image_shape[0],
image_shape[1], num_classes] shape array which one-hot encoedes each pixel's
class.
"""
class SemanticModel:
  num_shuffle = 10000
  learning_rate = 0.01
  def __init__(self, image_shape, num_classes, model_dir=None, l2_reg_scale=None):
    self.image_shape = list(image_shape)
    self.annotation_shape = self.image_shape[:2] + [1]
    assert(len(self.image_shape) == 3)
    self.num_classes = num_classes
    self.l2_reg_scale = l2_reg_scale

    feature_columns = [tf.feature_column.numeric_column(
      'image', shape=self.image_shape, dtype=tf.uint8)]
    
    self.params = {'feature_columns' : feature_columns}

    self.model_dir = model_dir

  def train(self, train_data, num_eval=100, test_data=None, overwrite=False,
            num_epochs=1, eval_secs=600, save_steps=100):
    """Train the model with tf Dataset object train_data. If test_data is not None,
    evaluate the model with it, and log the results (at INFO level).

    :train_data: tf.Dataset (raw) to use for training
    :num_eval: number of examples to reserve from train_data for
      evaluation. Ignored if eval_secs is 0.
    :test_data: tf.Dataset used for final testing
    :overwrite: overwrite the existing model
    :eval_secs: do evaluation every EVAL_SECS. Default = 600. Set to 0 for no
      evaluation.

    """
    assert num_eval >= 0 and eval_secs >= 0
    if eval_secs == 0:
      num_eval = 0

    if overwrite and self.model_dir is None:
      logger.warning("FAIL to overwrite; model_dir is None")

    if (overwrite and self.model_dir is not None
        and os.path.exists(self.model_dir)):
      rmtree(self.model_dir)
      
    if (overwrite and self.model_dir is not None
        and not os.path.exists(self.model_dir)):
      os.mkdir(self.model_dir)

    # Configure session
    run_options = tf.RunOptions(report_tensor_allocations_upon_oom = True)
    run_config = tf.estimator.RunConfig(model_dir=self.model_dir,
                                        save_checkpoints_steps=save_steps,
                                        log_step_count_steps=5)
    
    input_train = lambda : (
      train_data.skip(num_eval)
      .shuffle(self.num_shuffle)
      .batch(batch_size)
      .repeat(num_epochs)
      .make_one_shot_iterator()
      .get_next())

    # Train the model. (Might take a while.)
    model = tf.estimator.Estimator(model_fn=self.create(training=True),
                                   model_dir=self.model_dir,
                                   params=self.params,
                                   config=run_config)

    # TODO: initialize weights

    if num_eval == 0:
      logger.info("train...")
      model.train(input_fn=input_train)
    else:
      logger.info("train and evaluate...")
      input_eval = lambda : (
        train_data.take(num_eval)
        .batch(batch_size)
        .make_one_shot_iterator()
        .get_next())
      train_spec = tf.estimator.TrainSpec(input_fn=input_train)
      eval_spec = tf.estimator.EvalSpec(input_fn=input_eval,
                                        steps=num_eval // batch_size,
                                        throttle_secs=eval_secs)
      tf.estimator.train_and_evaluate(model, train_spec, eval_spec)
    
    if test_data is not None:
      logger.info("evaluating test accuracy")
      input_test = lambda : (
        test_data.batch(batch_size)
        .make_one_shot_iterator()
        .get_next())
      test_result = model.evaluate(input_fn=input_test)
      logger.info(test_result)

  def predict(self, test_data):
    """Return the estimator's predictions on test_data.

    """
    if self.model_dir is None:
      logger.warning("prediction FAILED (no model_dir)")
      return None

    input_pred = lambda : (
      test_data.batch(batch_size)
      .make_one_shot_iterator()
      .get_next())
  
    model = tf.estimator.Estimator(model_fn=self.create(training=False),
                                   model_dir=self.model_dir,
                                   params=self.params)

    predictions = model.predict(input_fn=input_pred)
    return predictions


  def create(self, training=True):
    """Create the model function for a custom estimator.

    """
    
    def model_function(features, labels, mode, params):
      images = tf.reshape(features, [-1] + self.image_shape)
      predicted_logits = self.infer(images, training=training)
      predictions = tf.reshape(tf.argmax(predicted_logits, axis=3),
                               [-1] + self.annotation_shape)
      predictions_one_hot = tf.one_hot(tf.reshape(
        predictions, [-1] + self.annotation_shape[:2]), self.num_classes)

      # In PREDICT mode, return the output asap.
      if mode == tf.estimator.ModeKeys.PREDICT:
        predicted_logits = tf.cast(255*predicted_logits, tf.uint8)
        return tf.estimator.EstimatorSpec(
          mode=mode, predictions={'image' : images,
                                  'logits' : predicted_logits,
                                  'annotation' : predictions})

      # Get "ground truth" for other modes.
      annotations = tf.cast(tf.reshape(labels, [-1] + self.annotation_shape),
                            tf.int64)
      annotations_one_hot = tf.one_hot(annotations[:,:,:,0], self.num_classes)

      logger.debug("annotations_one_hot: {}".format(annotations_one_hot.shape))
      logger.debug("predicted_logits: {}".format(predicted_logits.shape))

      # weight by class frequency
      weights = median_frequency_weights(annotations_one_hot, self.num_classes)
      weights = tf.argmax(weights * annotations_one_hot, axis=3)
      logger.debug("weights: {}".format(weights.shape))

      # Calculate loss:
      cross_entropy = tf.losses.softmax_cross_entropy(
        onehot_labels=annotations_one_hot,
        logits=predicted_logits,
        weights=weights)

      # Return an optimizer, if mode is TRAIN
      if mode == tf.estimator.ModeKeys.TRAIN:
        # TODO: momentum optimizer.
        optimizer = tf.train.AdamOptimizer(self.learning_rate)
        train_op = optimizer.minimize(loss=cross_entropy,
                                      global_step=tf.train.get_global_step())
        return tf.estimator.EstimatorSpec(mode=mode, 
                                          loss=cross_entropy, 
                                          train_op=train_op)
    
      assert mode == tf.estimator.ModeKeys.EVAL
      accuracy = tf.metrics.accuracy(labels=annotations,
                                     predictions=predictions)
      
      # TODO: somehow, we're getting very high (99.5%) accuracy for objId 1 (the
      # balls), but the prediction images are messed up somehow. Figure out why.
      eval_metrics = {'accuracy' : accuracy}
      for objId in range(self.num_classes):
        weights = tf.gather(annotations_one_hot, objId, axis=3)
        eval_metrics[f'class_{objId}_accuracy'] = tf.metrics.accuracy(
          labels=annotations_one_hot,
          predictions=predictions_one_hot,
          weights=weights)
    
      return tf.estimator.EstimatorSpec(mode=mode,
                                        loss=cross_entropy,
                                        eval_metric_ops=eval_metrics)

    return model_function

  def infer(self, images, training=True):
    raise NotImplementedError("SemanticModel subclass should implement inference().")



"""Implementation of UNet."""
class UNet(SemanticModel):
  def infer(self, images, training=True):  
    """The UNet architecture has two stages, up and down. We denote layers in the
    down-stage with "dn" and those in the up stage with "up," even though the
    up_conv layers are just performing regular, dimension-preserving
    convolution. "up_deconv" layers are doing the convolution transpose or
    "upconv-ing."

    """

    # block level 1
    dn_conv1_1 = self.conv(images, filters=64, training=training)
    dn_conv1_2 = self.conv(dn_conv1_1, filters=64, training=training)
    dn_pool1 = self.pool(dn_conv1_2)
    
    # block level 2
    dn_conv2_1 = self.conv(dn_pool1, filters=128, training=training)
    dn_conv2_2 = self.conv(dn_conv2_1, filters=128, training=training)
    dn_pool2 = self.pool(dn_conv2_2)
    
    # block level 3
    dn_conv3_1 = self.conv(dn_pool2, filters=256, training=training)
    dn_conv3_2 = self.conv(dn_conv3_1, filters=256, training=training)
    dn_pool3 = self.pool(dn_conv3_2)
    
    # block level 4
    dn_conv4_1 = self.conv(dn_pool3, filters=512, training=training)
    dn_conv4_2 = self.conv(dn_conv4_1, filters=512, training=training)
    dn_pool4 = self.pool(dn_conv4_2)
    
    # block level 5 (bottom). No max pool; instead deconv and concat.
    dn_conv5_1 = self.conv(dn_pool4, filters=1024, training=training)
    dn_conv5_2 = self.conv(dn_conv5_1, filters=1024, training=training)
    up_deconv5 = self.deconv(dn_conv5_2, filters=512)
    up_concat5 = tf.concat([dn_conv4_2, up_deconv5], axis=3)
    
    # block level 4 (going up)
    up_conv4_1 = self.conv(up_concat5, filters=512)
    up_conv4_2 = self.conv(up_conv4_1, filters=512)
    up_deconv4 = self.deconv(up_conv4_2, filters=256)
    up_concat4 = tf.concat([dn_conv3_2, up_deconv4], axis=3)
    
    # block level 3
    up_conv3_1 = self.conv(up_concat4, filters=256)
    up_conv3_2 = self.conv(up_conv3_1, filters=256)
    up_deconv3 = self.deconv(up_conv3_2, filters=128)
    up_concat3 = tf.concat([dn_conv2_2, up_deconv3], axis=3)
    
    # block level 2
    up_conv2_1 = self.conv(up_concat3, filters=128)
    up_conv2_2 = self.conv(up_conv2_1, filters=128)
    up_deconv2 = self.deconv(up_conv2_2, filters=64)
    up_concat2 = tf.concat([dn_conv1_2, up_deconv2], axis=3)
    
    # block level 1
    up_conv1_1 = self.conv(up_concat2, filters=64)
    up_conv1_2 = self.conv(up_conv1_1, filters=64)
    return self.conv(up_conv1_2, filters=self.num_classes,
                     kernel_size=[1, 1], activation=None)
    
  def conv(self, inputs, filters=64, kernel_size=[3,3], activation=tf.nn.relu,
           training=True):
    """Apply a single convolutional layer with the given activation function applied
    afterword. If l2_reg_scale is not None, specifies the Lambda factor for
    weight normalization in the kernels. If training is not None, indicates that
    batch_normalization should occur, based on whether training is happening.
    """

    if self.l2_reg_scale is None:
      regularizer = None
    else:
      regularizer = tf.contrib.layers.l2_regularizer(scale=self.l2_reg_scale)

    stddev = np.sqrt(2 / (np.prod(kernel_size) * filters))
    initializer = tf.initializers.random_normal(stddev=stddev)

    output = tf.layers.conv2d(
      inputs=inputs,
      filters=filters,
      kernel_size=kernel_size,
      padding="same",
      activation=activation,
      kernel_initializer=initializer,
      kernel_regularizer=regularizer)

    # normalize the weights in the kernel
    output = tf.layers.batch_normalization(
      inputs=output,
      axis=-1,
      momentum=0.9,
      epsilon=0.001,
      center=True,
      scale=True,
      training=training)
    
    return output
                 
  def pool(self, inputs):
    """Apply 2x2 maxpooling."""
    return tf.layers.max_pooling2d(inputs=inputs, pool_size=[2, 2], strides=2)

  def deconv(self, inputs, filters):
    """Perform "de-convolution" or "up-conv" to the inputs, increasing shape."""
    if self.l2_reg_scale is None:
      regularizer = None
    else:
      regularizer = tf.contrib.layers.l2_regularizer(scale=self.l2_reg_scale) 

    stddev = np.sqrt(2 / (2*2*filters))
    initializer = tf.initializers.random_normal(stddev=stddev)
    
    output = tf.layers.conv2d_transpose(
      inputs=inputs,
      filters=filters,
      strides=[2, 2],
      kernel_size=[2, 2],
      padding="same",
      activation=tf.nn.relu,
      kernel_initializer=initializer,
      kernel_regularizer=regularizer)

    return output
