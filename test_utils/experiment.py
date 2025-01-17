"""experiments is a tool for creating large, labeled training sets for semantic
segmentation and/or object detection, with the ray-tracing tool POV-Ray.

Dependencies:
* numpy
* POV-Ray
* vapory

On masks and annotations:
A "mask" is a tuple of arrays, such as those returned by skimage.draw functions,
which index into the experiment's image space.
An "annotation" is an array with the same height and width as the experiment's
image, containing information about an image and the objects in it. A shallow
annotation contains just class labels at every pixel. A deeper annotation
contains scalar information about an object at every pixel, such as the distance
to its center.

"""

import logging
logger = logging.getLogger('experiment')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(levelname)s:experiment:%(message)s'))
logger.addHandler(handler)

import numpy as np
import vapory
import os
import matplotlib.pyplot as plt
from skimage import draw
from inspect import signature
import subprocess as sp
import tensorflow as tf
from artifice.utils import img, vid
from artifice import dat


INFINITY = 10e9


def normalize(X):
  """Normalize a vector.

  :param X: 1-D numpy vector
  :returns: normalized vector
  :rtype: same as X

  """
  X = np.array(X)
  assert(len(X.shape) == 1)
  return X / np.linalg.norm(X)

def perpendicular(X):
  """Return a unit vector perpendicular to X in R^3."""
  X = np.array(X)
  assert(len(X.shape) == 1)
  return normalize(np.array([X[1] - X[2],
                             X[2] - X[0],
                             X[0] - X[1]]))

def quadratic_formula(a,b,c):
  """Return the two solutions according to the quadratic formula. 

  :param a: 
  :param b: 
  :param c: 
  :returns: two real solutions, or None

  """
  sqrt_term = b**2 - 4*a*c
  if sqrt_term < 0:
    return None

  return (-b + np.sqrt(sqrt_term)) / (2*a), (-b - np.sqrt(sqrt_term)) / (2*a)


class DynamicObject:
  """An ImageObject is a wrapper around a Vapory object. This may be used for any
  objects in a scene which change from image to image but are not being tracked.
  The vapory object itself is created on every __call__().

  * vapory_object: the vapory class this ExperimentObject represents.
  * object_args: either a tuple, containing all required arguments for creating
    vapory_object, or a function which creates this tuple (allowing for
    non-determinism), as well as any others that should change on each
    call. This function may optionally take an argument (such as a time step).
  * args: any additional args, passed on as vapory_object(*object_args + args)
  * kwargs: additions keyword arguments are passed onto the vapory object, the
    same on every call.

  self.args stores the most recent args as a list. This is instantiated as [],
  and is used by Experiment.annotate_and_label() to generate the mask of the object.

  """
  def __init__(self, vapory_object, object_args, *args, **kwargs):
    """FIXME! briefly describe function

    :param vapory_object: 
    :param object_args: 
    :returns: 
    :rtype: 

    """
    self.vapory_object = vapory_object
    
    if callable(object_args):
      self.get_args = object_args
      self.do_time_step = len(signature(object_args).parameters) == 1
    elif type(object_args) == tuple:
      self.get_args = lambda : object_args
      self.do_time_step = False
    else:
      raise RuntimeError("`object_args` is not a tuple or function")

    self.other_args = list(args) + sum([list(t) for t in kwargs.items()], [])
    self.args = []

  def __call__(self, t=None):
    """Return an instance of the represented vapory object, ready to be inserted
    into a scene.

    TODO: allow arguments to be passed in, overriding get_args. Optional.
    """
    if self.do_time_step:
      assert(t is not None)
      self.args = list(self.get_args(t)) + self.other_args
    else:
      self.args = list(self.get_args()) + self.other_args
    return self.vapory_object(*self.args)

  
class ExperimentObject(DynamicObject):
  """An ExperimentObject is a wrapper around a Vapory object, and it represents a
  marker for detection. An ExperimentObject should be used, rather than a mere
  ImageObject or vapory object, whenever the object is being tracked (needs a
  mask). Unlike an ImageObject, every ExperimentObject has a class associated
  with it (an integer >0, since 0 is the background class).

  args:
    vapory_object: the vapory class this ExperimentObject represents.
    object_args: either a tuple, containing all required arguments for creating
      vapory_object, or a function which creates this tuple (allowing for
      non-determinism), as well as any others that should change on each call.
    semantic_label: numerical class of the object, used for generating an
      annotation. default=1.
    args: any additional args, passed on as vapory_object(*object_args + args)
    kwargs: additions keyword arguments are passed onto the vapory object, the
      same on every call.

  """

  def __init__(self, vapory_object, object_args, *args, semantic_label=1, **kwargs):
    super().__init__(vapory_object, object_args, *args, **kwargs)
    assert(semantic_label > 0)
    self.semantic_label = int(semantic_label)
    
  def compute_mask(self, experiment):
    """Compute the mask of the ExperimentObject, given Experiment
    `experiment`.

    This should be overwritten by subclasses, for each type of vapory
    object. Each object returns the indices of the experiment scene that it
    contains (as in skimage.draw). It is up to the Experiment to decide, in the
    case of occlusions, which object is in front of the other.

    """
    raise NotImplementedError("compute_mask is specific to each vapory object.")

  def compute_location(self, experiment):
    """Compute the image-space location of the object. This should be an
    unambiguous location, such as the center.
    """
    raise NotImplementedError()

  def compute_label(self, experiment):
    """Compute the label for this object. Usually just location and sometimes
    orientation."""
    raise NotImplementedError()


class ExperimentSphere(ExperimentObject):
  """An ExperimentSphere, representing a vapory.Sphere.

  args:
    vapory_object: the vapory class this ExperimentObject represents.
    object_args: either a tuple, containing all required arguments for creating
      vapory_object, or a function which creates this tuple (allowing for
      non-determinism), as well as any others that should change on each call.
    semantic_label: numerical class of the object, used for generating an
      annotation. default=1.
    args: any additional args, passed on as vapory_object(*object_args + args)
    kwargs: additions keyword arguments are passed onto the vapory object, the
      same on every call.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(vapory.Sphere, *args, **kwargs)
    self.center = self.radius = None

  def __call__(self, t=None):
    """Record the center and radius of the sphere."""
    vapory_object = super().__call__(t)
    self.center = np.array(self.args[0])
    self.radius = self.args[1]
    return vapory_object
  
  def distance_to_surface(self, Xi, experiment):
    """Given a point Xi = [x,y] in image-space, compute the distance from
    experiment.camera_location to the near-surface of the sphere.

    If Xi is not on the surface, return "infinity" (actually 1bil).
    """
    assert(len(self.args) != 0)
    const = experiment.camera_location - self.center
    v = experiment.unproject(Xi)

    a = np.linalg.norm(v)**2
    b = 2*np.dot(const, v)
    c = np.linalg.norm(const)**2 - self.radius**2

    ts = quadratic_formula(a,b,c)
    if ts == None:
      return INFINITY
    t1, t2 = ts

    d1 = np.linalg.norm(t1*v)
    d2 = np.linalg.norm(t2*v)

    return min(d1, d2)

  def compute_mask(self, experiment):
    """Compute the mask for an ExperimentSphere, placed in experiment. Returns rr,
    cc, which are list of indices to access the image (as from skimage.draw),
    and dd: the distance from camera to object along each pixel in (rr,cc).

    """
    assert(len(self.args) != 0)
    center = experiment.project(self.center)
    center_to_edge = self.radius * perpendicular(
      experiment.camera_to(self.center))
    radius_vector = (experiment.project(self.center + center_to_edge)
                     - experiment.project(self.center))
    radius = np.linalg.norm(radius_vector)
    
    rr, cc = draw.circle(center[0], center[1], radius,
                         shape=experiment.image_shape[:2])
    
    dd = np.empty(rr.shape[0], dtype=np.float64)
    for i in range(dd.shape[0]):
      dd[i] = self.distance_to_surface([rr[i], cc[i]], experiment)
      
    return rr, cc, dd

  def compute_location(self, experiment):
    assert(len(self.args) != 0)
    return experiment.project(self.center)

  def compute_label(self, experiment):
    """Computes the label for the object
    :experiment: the Experiment containing this object

    :returns: `[obj_id, x pos, y pos, theta, x_scale, y_scale]`

    """
    label = np.empty((experiment.label_dimension,), dtype=np.float32)
    label[0] = float(self.semantic_label)
    label[1:3] = self.compute_location(experiment)
    label[3] = 0
    label[4] = 1
    label[5] = 1
    return label
  
class Experiment:
  """An Experiment contains information for generating a dataset, which is done
  using self.run(). It has variations that affect the output labels.
  
  :param image_shape: (rows, cols) shape of the output images, determines the
  aspect ratio of the camera, default=(512,512). Number of channels determined
  by `mode`
  :param mode: image mode to generate, default='L' (8-bit grayscale)
  :param num_classes: number of classes to be detected, INCLUDING the background class.
  :param N: number of images to generate, default=1000
  :param output_format: filetype to write, default='tfrecord'. Can be a list of
  filetypes, in which case the same data will be written to each.
  :param fname: name of output file, without extension. Ignored if included.
  :param camera_multiplier: controls how far out the camera is positioned, as a
  multiple of image_shape[1] (vertical pixels), default=4 (far away)
  :param noisify: add Poisson noise to each frame depending on the frame rate.

  Image `mode` is according to PIL.Image. Valid inputs are:
  * L (8-bit pixels, black and white)
  * RGB (3x8-bit pixels, true colour)
  Other modes to be supported later, including:

  The camera will be placed in each experiment such that the <x,y,0> plane is
  the image plane, with one unit of distance corresponding to ~1 pixel on that
  plane.

  self.objects is a list of ExperimentObjects that are subject to change,
  whereas self.static_objects is a list of vapory Objects ready to be inserted
  in the scene, as is.

  """

  supported_modes = {'L', 'RGB'}
  pix_fmts = {'L' : 'gray', 'RGB' : 'rgb8'}
  supported_formats = {'tfrecord', 'mp4', 'png'}
  included = ["colors.inc", "textures.inc"]
  label_dimension = 6

  def __init__(self, image_shape=(512,512), mode='L', num_classes=2, N=1000,
               output_format='tfrecord', data_root="data/tmp", camera_multiplier=4,
               fps=1, noisify=True):
    self.N = int(N)
    self.noisify = noisify

    self.image_shape = tuple(image_shape)
    assert(len(self.image_shape) == 2)

    assert(mode in self.supported_modes)
    self.mode = mode

    # output formats
    if type(output_format) in [list, set]:
      self.output_formats = set(output_format)
    else:
      assert(type(output_format) == str)
      self.output_formats = {output_format}
    assert(all([f in self.supported_formats for f in self.output_formats]))

    # set fname, without extension
    self.data_root = data_root
    if not os.path.exists(self.data_root):
      os.makedirs(self.data_root)

    assert(camera_multiplier > 0)
    self.camera_multiplier = camera_multiplier

    assert(num_classes > 0)
    self.num_classes = num_classes # TODO: unused
    self.fps = int(fps)
    
    self._set_camera()
    
    # The objects in the scene should be added to by the subclass.
    self.experiment_objects = [] # ExperimentObject instances
    self.dynamic_objects = []    # DynamicObject instances
    self.static_objects = []     # vapory object instances

  def add_object(self, obj):
    """Adds obj to the appropriate list, according to the type of the object.

    If obj is not an ExperimentObject or a vapory object, behavior is
    undefined.
    """
    if issubclass(type(obj), ExperimentObject):
      self.experiment_objects.append(obj)
    elif type(obj) == DynamicObject:
      self.dynamic_objects = []
    else:
      self.static_objects.append(obj)
  
  def _set_camera(self):
    """Sets the camera dimensions of the Experiment so that the output image has
    `image_shape`. Also sets the camera projection matrix. Should only be called
    by __init__().

    """

    camera_distance = self.image_shape[0]*self.camera_multiplier
    location = [0, 0, -camera_distance]
    direction = [0, 0, 1]       # POV-Ray direction vector
    aspect_ratio = self.image_shape[0] / self.image_shape[1] # aspect ratio
    right = [aspect_ratio, 0, 0]                             # POV-Ray vector
    half_angle_radians = np.arctan(1 / (2*self.camera_multiplier))
    
    # (Szeliski 53)
    focal_length = self.image_shape[1] / (2*np.tan(half_angle_radians))
    
    # Set the camera projection matrix.
    K = np.array(
      [[focal_length, 0, self.image_shape[0]/2],
       [0, aspect_ratio*focal_length, self.image_shape[1]/2],
       [0, 0, 1]])
    T = np.array(
      [[0],
       [0],
       [camera_distance]])
    R = np.array(
      [[0, -1, 0],
       [1, 0, 0],
       [0, 0, 1]])
    P = K @ np.concatenate((R, T), axis=1)
    self._camera_WtoI = np.concatenate((P, [[0, 0, 0, 1]]), axis=0)
    self._camera_ItoW = np.linalg.inv(self._camera_WtoI)

    self.camera_location = np.array(location)

    self.camera = vapory.Camera('location', location,
                                'direction', direction,
                                'right', right,
                                'angle', 2*np.degrees(half_angle_radians))

  def camera_to(self, X):
    """Get the world-space vector from the camera to X"""
    assert(len(X) == 3)
    return np.array(X) - self.camera_location
  
  def project(self, X):
    """Project the world-space POINT X = [x,y,z] to image-space.
    Return the [i,j] point in image-space (as a numpy array).
    """
    assert(len(X) == 3)
    Xi = self._camera_WtoI @ np.concatenate((np.array(X), [1]))
    return np.array([Xi[0]/Xi[2], Xi[1]/Xi[2]])

  def unproject_point(self, Xi, disparity=1):
    """From index space point Xi = [x,y], unproject back into world-space. Note
    that since an unambiguous 3D point cannot be recovered, this should be used
    only to recover a ray associated with a given pixel in image-space.

    The "disparity" argument controls this ambiguity. Different disparities will
    yield different points along the same ray.
    """
    assert(len(Xi) == 2)
    Xi = np.array(Xi)
    X = self._camera_ItoW @ np.array([Xi[0], Xi[1], 1, disparity])
    return (X / X[3])[:3]

  def unproject(self, Xi):
    """From index space point Xi = [x,y], unproject back into world-space. 

    Due to 3D-2D ambiguity, an image-space corresponds to a ray in
    world-space. Returns a unit-vector along this ray. Together with camera
    location, this can recover any point along the ray.

    """
    Xi = np.array(Xi)
    a = self.unproject_point(Xi)
    b = self.unproject_point(Xi, disparity = 2)
    V = normalize(a - b)
    if V[2] == 0:
      return V
    else:
      return V * V[2] / abs(V[2]) # ensure V points toward +z

  def unproject_to_image_plane(self, Xi):
    """Unproject back to the world-space point which lies on the image plane.

    :param Xi: [i,j] index-space point
    :returns: 3-vector world-space position
    :rtype: numpy array

    """
    Xi = np.array(Xi)
    u_hat = self.unproject(Xi)
    v = self.camera_location
    mag_v = np.linalg.norm(v)
    cos_th = np.dot(u_hat,v) / mag_v
    u = (mag_v / cos_th) * u_hat
    return v + u
  
  def annotate_and_label(self):
    """Computes the annotation and label for the scene.

    Based on the most recent vapory objects created.

    The first channel of the annotation always marks class labels.
    Further channels are more flexible, but in general the second channel should
    enocde distance. Here, we use an object's compute_location method to get
    the distance at every point inside an object's annotation.
    
    The label has a row for every object in the image (which can get flattened
    for a network). The first element of each row contains the semantic label of
    the object, if it is in the example, or 0 otherwise. TODO: implement this.
    
    The determination of whether an object is "in the example" is subjective. We
    consider "in the example" to mean that the object's "center" location is not
    occluded.
    
    TODO: currently, only modifies masks due to occlusion by other objects in
    experiment_objects. This is usually sufficient, but in some cases, occlusion
    may occur from static or untracked objects.
    
    """
    label = np.zeros((len(self.experiment_objects), self.label_dimension),
                     dtype=np.float32)
    annotation = np.zeros((self.image_shape[0], self.image_shape[1], 1),
                          dtype=np.int64)
    object_distance = INFINITY * np.ones(annotation.shape[:2], dtype=np.float64)

    for i, obj in enumerate(self.experiment_objects):
      label[i] = obj.compute_label(self)
      rr, cc, dd = obj.compute_mask(self)
      for r, c, d in zip(rr, cc, dd):
        # TODO: overwrite objects in the background, if they're not visible.
        if d < object_distance[r, c]:
          object_distance[r, c] = d
          annotation[r, c, 0] = obj.semantic_label

    return annotation, label
  
  def render_scene(self, t=None):
    """Renders a single scene, applying the various perturbations on each
    object/light source in the Experiment.

    :returns: `(example, annotation)` pair.

    TODO:
    Call the make_targets() function, implemented by subclasses, that uses
    the object locations, orientations, etc. set by render_scene, to calculate
    the targets.
    """

    dynamic_objects = [obj(t) for obj in self.dynamic_objects]
    experiment_objects = [obj(t) for obj in self.experiment_objects]
    all_objects = self.static_objects + dynamic_objects + experiment_objects
    vap_scene = vapory.Scene(self.camera, all_objects, included=self.included)

    # image, annotation ndarrays of np.uint8s.
    image = vap_scene.render(height=self.image_shape[0], width=self.image_shape[1])
    if self.mode == 'L':
      image = img.grayscale(image)

    # Add noise
    if self.noisify:
      peak = 5000             # TODO: make fps dependent.
      image = np.random.poisson(image.astype(np.float64) / 255. * peak)
      image = (image / peak * 255.).astype(np.uint8)
      
    # compute annotation, label using most recently used args, produced by the
    # render call
    annotation, label = self.annotate_and_label()

    return (image, label), annotation
    
  def run(self, verbose=None):
    """Generate the dataset in each format.
    """

    if verbose is not None:
      logger.warning("verbose is depricated")

    if len(self.output_formats) == 0:
      # TODO: raise error?
      return

    # Instantiate writers and fnames for each format
    if 'png' in self.output_formats:
      image_dir = os.path.join(self.data_root, 'images/')
      annotation_dir = os.path.join(self.data_root, 'annotations/')
      if not os.path.exists(image_dir):
        os.makedirs(image_dir)
      if not os.path.exists(annotation_dir):
        os.makedirs(annotation_dir)
      label_path = os.path.join(self.data_root, 'labels.npy')
      labels = None
      logger.info("writing images to {}".format(image_dir))
    
    if 'tfrecord' in self.output_formats:
      tfrecord_name = os.path.join(self.data_root, 'data.tfrecord')
      tfrecord_writer = tf.python_io.TFRecordWriter(tfrecord_name)
      logger.info("writing tfrecord to {}".format(tfrecord_name))

    if 'mp4' in self.output_formats:
      mp4_image_name = os.path.join(self.data_root, 'data.mp4')
      mp4_image_writer = vid.MP4Writer(
        mp4_image_name, self.image_shape[:2], fps=self.fps)
      logger.info("writing video to {}".format(mp4_image_name))
      
    # step through all the frames, rendering each scene with time-dependence if
    # necessary.
    for t in range(self.N):
      logger.info("Rendering scene {} of {}...".format(t, self.N))
      example, annotation = self.render_scene(t)
      image, label = example
      logger.debug(f"label: {label}")

      if 'png' in self.output_formats:
        fname = f"{str(t).zfill(5)}"
        img.save(os.path.join(image_dir, fname + '.png'), np.squeeze(image))
        np.save(os.path.join(annotation_dir, fname + '.npy'), annotation)
        if labels is None:
          labels = np.empty((self.N,) + label.shape)
        labels[t] = label
      
      if 'tfrecord' in self.output_formats:
        e = dat.proto_from_scene(scene)
        tfrecord_writer.write(e)
        
      if 'mp4' in self.output_formats:
        mp4_image_writer.write(image)

        
    if 'png' in self.output_formats:
      logger.info("Finished writing images.")
      np.save(os.path.join(self.data_root, "labels.npy"), labels)
      
    if 'tfrecord' in self.output_formats:
      tfrecord_writer.close()
      logger.info("Finished writing tfrecord.")

    if 'mp4' in self.output_formats:
      mp4_image_writer.close()
      logger.info("Finished writing video.")

    
def main():
  """For testing purposes"""
  pass
  

if __name__ == '__main__': main()
