"""Util functions for manipulating images in artifice.
"""

import numpy as np
from PIL import Image
from skimage import draw, transform
import scipy
import logging
import matplotlib.pyplot as plt

logger = logging.getLogger('artifice')

def draw_x(image, x, y, size=12, channel=0):
  """Draw a x at the x,y location with `size`

  :param image: image to draw on, at least 3 channels, float valued in [0,1)
  :param x: x position
  :param y: y position
  :param size: marker diameter in pixels, default 12
  :param channel: which channel(s) to draw in. Default (0) makes a red x
  :returns: 
  :rtype: 

  """
  h = int(size / (2*np.sqrt(2)))
  i = int(x)
  j = int(y)
  rr, cc, val = draw.line_aa(i-h, j-h, i+h, j+h)
  rr, cc, val = get_inside(rr, cc, image.shape, vals=val)
  image[rr,cc,channel] = val
  rr, cc, val = draw.line_aa(i-h, j+h, i+h, j-h)
  rr, cc, val = get_inside(rr, cc, image.shape, vals=val)
  image[rr,cc,channel] = val
  return image

def draw_t(image, x, y, size=12, channel=1):
  """Draw a x at the x,y location with `size`

  :param image: image to draw on, at least 3 channels, float valued in [0,1)
  :param x: x position
  :param y: y position
  :param size: marker diameter in pixels, default 12
  :param channel: which channel(s) to draw in. Default (1) makes a green x
  :returns: 
  :rtype: 

  """
  h = size // 2
  i = int(np.floor(x))
  j = int(np.floor(y))
  rr, cc, val = draw.line_aa(i-h, j, i+h, j)
  rr, cc, val = get_inside(rr, cc, image.shape, vals=val)
  image[rr,cc,channel] = val
  rr, cc, val = draw.line_aa(i, j-h, i, j+h)
  rr, cc, val = get_inside(rr, cc, image.shape, vals=val)
  image[rr,cc,channel] = val
  return image

def indices_from_regions(regions, num_objects):
  """Given an image-shaped annotation of regions, get indices of regions.

  :param regions: 
  :returns: `[(xs_0,ys_0),(xs_1,ys_1),...]` indices for each region
  :rtype: list of two-tuples, each with a list of ints

  """
  regions = np.squeeze(regions)
  indices = []
  for i in range(num_objects + 1):
    indices.append(np.where(regions == i))
  return indices

def fill_negatives(image):
  """Fill the negative values in background with gaussian noise.
  
  :param image: a numpy array with negative values to fill
  
  """
  image = image.copy()
  indices = image >= 0
  mean = image[indices].mean()
  std = image[indices].std()
  
  indices = image < 0
  image[indices] = np.random.normal(mean, std, size=image[indices].shape)
  return image

def inside(xs, ys, shape):
  """Returns a boolean array for which indices are inside shape.

  :param xs: numpy arrays
  :param ys: numpy arrays
  :param shape: image shape to compare against, using first two dimensions
  :returns: 1-D boolean array

  """
  over = np.logical_and(xs >= 0, ys >= 0)
  under = np.logical_and(xs < shape[0], ys < shape[1])
  return np.logical_and(over, under)

def get_inside(xs, ys, shape, vals=None):
  """Get the indices that are inside image's shape.

  :param xs: x indices
  :param ys: y indices
  :param shape: image shape to compare with
  :returns: a subset of indices.

  """
  xs = np.array(xs)
  ys = np.array(ys)
  which = inside(xs, ys, shape)
  if vals is None:
    return xs[which], ys[which]
  else:
    return xs[which], ys[which], vals[which]

def grayscale(image):
  """Convert an n-channel, 3D image to grayscale.
  
  Use the [luminosity weighted average]
  (https://www.johndcook.com/blog/2009/08/24/algorithms-convert-color-grayscale/)
  if there are three channels. Otherwise, just use the average.

  :param image: image to convert
  :returns: new grayscale image.

  """
  image = np.array(image)
  out_shape = (image.shape[0], image.shape[1], 1)
  if image.ndim == 2:
    return image.reshape(*out_shape)

  assert(image.ndim == 3)
  
  if image.shape[2] == 3:
    W = np.array([0.21, 0.72, 0.07])
    return (image * W).mean(axis=2).reshape(*out_shape).astype(np.uint8)
  else:
    return image.mean(axis=2).reshape(*out_shape).astype(np.uint8)

def rgb(image):
  """Convert grayscale image to rgb.

  :param image: 
  :returns: 
  :rtype: 

  """
  image = np.squeeze(image)
  if image.ndim == 2:
    return np.stack((image, image, image), axis=-1)
  elif image.ndim == 3 and image.shape[2] > 3:
    return image[:,:,:3]
  elif image.ndim == 3 and image.shape[2] == 3:
    return image.copy()
  else:
    raise RuntimeError(f"couldn't handle image shape {image.shape}")


def open_as_array(fname):
  im = Image.open(fname)
  if im.mode == 'L':
    image = np.array(im).reshape(im.size[1], im.size[0])
  elif im.mode == 'RGB':
    image = np.array(im).reshape(im.size[1], im.size[0], 3)
  elif im.mode == 'P':
    image = np.array(im.convert('RGB')).reshape(im.size[1], im.size[0], 3)
  elif im.mode == 'RGBA':
    image = np.array(im.convert('RGB')).reshape(im.size[1], im.size[0], 3)
  else:
    raise NotImplementedError("Cannot create image mode '{}'".format(im.mode))
  return image


def as_float(image, atleast_3d=True):
  """Return image as a grayscale float32 array at least 3d, scaled to [0,1]."""
  if image.dtype in [np.float32, np.float64]:
    image = image.astype(np.float32)
  elif image.dtype in [np.uint8, np.int32, np.int64]:
    image = image.astype(np.float32) / 255.
  else:
    raise ValueError(f"image dtype '{image.dtype}' not allowed")
  if atleast_3d:
    return np.atleast_3d(image)
  else:
    return image

  
def open_as_float(image_path):
  return as_float(open_as_array(image_path), atleast_3d=False)


def save(fname, image):
  """Save the array image to png in fname."""
  if image.dtype in [np.float32, np.float64]:
    image = np.uint8(255*image)
  im = Image.fromarray(image)
  im.save(fname)




