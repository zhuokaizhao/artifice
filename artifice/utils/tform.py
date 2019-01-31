"""Transformation utils, used to build up augmentations."""

from artifice.utils import img, tfimg
import tensorflow as tf
import logging
from scipy import ndimage
import numpy as np

logger = logging.getLogger('artifice')

class Transformation():
  """A transformation is a callable that takes tensors representing an example
  (usually a scene) and returns a new pair. It should be mappable over a
  tf.data.Dataset.

  Transformations are meant to be aggressively subclassed. This allows families
  of transformations which, although not totally identitcal, to belong
  together. These can define the transform as a method rather than passing it in
  on initialization.

  Transformations can be composed (or "added together") using the "+"
  operator.

  Rather than pass in a function, subclasses may optionally define a "transform"
  method, which is taken at instantiation. This is supported but not preferred.

  """

  def __init__(self, transform=lambda scene : scene, **kwargs):
    """
    :param transforms: a transformation function or an iterable of them. Ignored if
      object has a "transform" method.
    :param which_examples: indices of examples in the dataset on which to apply
      the transformation. If None, maps over the whole dataset. 
    
    """

    if hasattr(self, 'transform'):
      assert callable(self.transform)
      self._transforms = [lambda scene : self.transform(scene)]
    elif callable(transform):
      self._transforms = [transform]
    elif hasattr(transform, '__iter__'):
      self._transforms = list(transform)
    else:
      raise ValueError()

    self._kwargs = kwargs

  def __call__(self, scene):
    for transform in self._transforms:
      scene = transform(scene)
    return scene

  def apply(self, dataset, num_parallel_calls=None):
    """Apply the transformation across a dataset.

    :param dataset: tf.data.Dataset instance
    :param num_parallel_calls: passed to `dataset.map`
    :returns: new dataset
    :rtype: 

    """
    return dataset.map(self, num_parallel_calls=num_parallel_calls)

  def __add__(self, other):
    return Transformation(self._transforms + other._transforms)

  def __radd__(self, other):
    if other == 0:
      return self
    else:
      return self.__add__(other)


"""For many transformations, Simple- and ImageTransformations should be
sufficient, and they may instantiated with transform functions on their own,
depending on whether that transform should applied to both image and annotation
(SimpleTransformation) or to the image alone (ImageTransformation)

Transformations that treat image and annotation separately should inherit from
Transformation directly.
"""
class SimpleTransformation(Transformation):
  """Applies the same tensor function to both image and annotation, clipping image
  values. Applies a different transform function to the labels

  """
  def __init__(self, image_transform, label_transform):
    def transform(scene):
      image, (annotation, label)
      image = image_transform(image)
      image = tf.clip_by_value(image,
                               tf.constant(0, dtype=image.dtype),
                               tf.constant(1, dtype=image.dtype))
      return image, (image_transform(annotation), label_transform(label))
    super().__init__(transform)

class ImageTransformation(Transformation):
  """Applies a tensor function to the image (and clips values), leaving annotation
  and label.

  """
  def __init__(self, function):
    def transform(scene):
      image, (annotation, label) = scene
      image = function(image)
      image = tf.clip_by_value(image,
                               tf.constant(0, dtype=image.dtype),
                               tf.constant(1, dtype=image.dtype))
      return image, (annotation, label)
    super().__init__(transform)


class FlipLeftRight(Transformation):
  def __init__(self):
    def transform(scene):
      image, (annotation, label) = scene
      image = tf.flip_left_right(image)
      annotation = tf.flip_left_right(annotation)
      label[:,2] = image.shape[1] - label[:,2]
      return image, (annotation, label)
    super().__init__(transform)
      

class FlipUpDown(Transformation):
  def __init__(self):
    def transform(scene):
      image, (annotation, label) = scene
      image = tf.flip_up_down(image)
      annotation = tf.flip_up_down(annotation)
      label[:,1] = image.shape[0] - label[:,1]
      return image, (annotation, label)
    super().__init__(transform)


"""The following are transformations that must be instantiated with a
parameter separate from the arguments given to the transformation function."""
class AdjustBrightness(ImageTransformation):
  """Adjust the brightness of the image by delta."""
  def __init__(self, delta):
    def transform(image):
      return tf.image.adjust_brightness(image, delta)
    super().__init__(transform)


class AdjustMeanBrightness(ImageTransformation):
  """Adjust the mean brightness of grayscale images to mean_brightness. Afterward,
  clip values as appropriate. Thus the final mean brightness might not be the
  value passed in. Keep this in mind. 

  """
  def __init__(self, new_mean): 
    def transform(image):
      mean = tf.reduce_mean(image)
      delta = tf.constant(new_mean, dtype=mean.dtype) - mean
      return tf.image.adjust_brightness(image, delta)
    super().__init__(transform)


    

class ObjectTransformation():
  """Generate a new example by extracting and then transforming individual
  objects. Should be run on numpy arrays rather than tensors, see
  DataAugmenter.

  """
  def __init__(self, **kwargs):
    # self.inpainter = kwargs.get('inpainter', inpaint.background)
    self.background_image = kwargs.get('background_image')
    self.num_classes = kwargs.get('num_classes', 2)

  def __call__(self, *args, **kwargs):
    return self.transform(*args, **kwargs)

  def transform(self, scene, new_label, **kwargs):
    """Transforms `scene` to match `new_label`.

    :param scene: (image, (annotation, label)) tensor tuple
    :param new_label: use instead of `self.new_label`
    :returns: transformed scene
    :rtype: tuple of tensors

    """
    image, (annotation, label) = scene
    new_image = image.copy()
    new_annotation = annotation.copy()
    
    components = img.connected_components(annotation, num_classes=self.num_classes)
    
    component_ids = [set() for _ in range(self.num_classes)]
    
    for i in kwargs.get('object_order', range(new_label.shape[0])):
      obj_label = label[i]
      new_obj_label = new_label[i]
      indices = img.connected_component_indices(
        annotation, obj_label[0], obj_label[1:3],
        num_classes=self.num_classes,
        components=components,
        component_ids=component_ids)
      if indices is None:
        continue;

      # Center the object in the image
      centering = np.array([image.shape[0] // 2 - obj_label[1],
                            image.shape[1] // 2 - obj_label[2], 0])
      centered_image = ndimage.interpolation.shift(image, centering)
      centered_annotation = ndimage.interpolation.shift(
        annotation[:,:,0], centering[:2], order=0)
      centered_indices = indices + centering[:2]

      # TODO: cut off unnecessary part of image, only cropping that's necessary
      
      angle = np.degrees(new_obj_label[3] - obj_label[3])
      rotated_image = ndimage.interpolation.rotate(
        centered_image, angle, reshape=False)
      rotated_annotation = ndimage.interpolation.rotate(
        centered_annotation, angle, reshape=False, order=0)

      # zoom in on image, according to x_scale, y_scale in label.
      # Note: new image will NOT have the same shape.
      zoom = new_obj_label[4:6] / obj_label[4:6]
      zoomed_image = ndimage.zoom(rotated_image, [zoom[0], zoom[1], 0])
      zoomed_annotation = ndimage.zoom(rotated_annotation, zoom, order=0)

      # Translate object to ultimate position in index space
      shift = np.array([new_obj_label[1] - image.shape[0] // 2,
                        new_obj_label[2] - image.shape[1] // 2, 0])
      shifted_image = ndimage.interpolation.shift(zoomed_image, shift)
      shifted_annotation = ndimage.interpolation.shift(
        zoomed_annotation, shift[:2], order=0)

      # Get the new indices from the transformed annotation
      new_indices = img.connected_component_indices(
        shifted_annotation, new_obj_label[0], new_obj_label[1:3])
      new_indices = img.get_inside(new_indices, image)

      logger.debug(f"new_indices: {new_indices}")

      # Erase the object from the image, insert it into the new location.
      new_image = img.inpaint_image_background(
        new_image, indices, background_image = self.background_image)
      new_image[new_indices] = shifted_image[new_indices]

      # Erase the object from the annotation, insert it into the new location.
      new_annotation = img.inpaint_annotation_background(new_annotation, indices)
      new_annotation = img.inpaint_annotation(
        new_annotation, new_indices, new_obj_label[1:3],
        semantic_label=new_obj_label[0])

    return new_image, (new_annotation, new_label)


# TODO: ObjectTranslation, ObjectRotation, and ObjectScaling subclassed from
# ObjectTransformation. The point is mostly moot, since ObjectTransformation
# generalizes over all of them, but some granularity might be useful in the
# future.
class ObjectTranslation(ObjectTransformation):
  def __init__(self):
    raise NotImplementedError

class ObjectRotation(ObjectTransformation):
  def __init__(self):
    raise NotImplementedError

class ObjectScaling(ObjectTransformation):
  def __init__(self):
    raise NotImplementedError


# Transformation instances.
identity = Transformation()
flip_left_right = FlipLeftRight()
flip_up_down = FlipUpDown()
invert_brightness = ImageTransformation(lambda image : 1 - image)


