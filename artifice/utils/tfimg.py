"""Utils relating to images and annotations as tf.Tensors."""

import tensorflow as tf

def connected_components(annotation, num_classes=2):
  """
  :annotation: 3D image annotation, where the first channel contains semantic
    labels.
  :num_classes: number of classes in the annotation. Default=2.
  
  Returns: a tensor of connected components, with a different channel for every
  semantic class (including background).

  """

  components = []
  for obj_id in range(num_classes):
    components.append(tf.contrib.images.connected_components(
      tf.equal(annotation[:,:,0], tf.constant(obj_id, annotation.dtype))))
    
  return tf.stack(components, axis=3, name='connected_components')


def inside(indices, image):
  """Returns a boolean array for which indices are inside image.shape.
  
  :indices: 2D tensor of indices. Fast axis must have same dimension as shape.
  :image: image to get the 
  
  Returns: 1-D boolean tensor
  
  """
  
  over = tf.greater_equal(indices, tf.constant(0, dtype=indices.dtype))
  under = tf.less(indices, tf.shape(image))
  return tf.reduce_any(tf.logical_and(over, under), axis=1, 
                       name='inside')


def get_inside(indices, image):
  """Get the indices that are inside image's shape.
  :indices: 2D tensor of indices. Fast axis must have same dimension as shape.
  :image: image to get the 
  
  Returns: a subset of indices.
  
  """
  
  return tf.gather(indices, tf.where(inside(indices, image)), 
                   name='get_inside')

