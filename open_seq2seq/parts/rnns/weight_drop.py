import tensorflow as tf

class WeightDropLayerNormBasicLSTMCell(tf.contrib.rnn.RNNCell):
  """LSTM unit with layer normalization and recurrent dropout.
  This class adds layer normalization and recurrent dropout to a
  basic LSTM unit. Layer normalization implementation is based on:
    https://arxiv.org/abs/1607.06450.
  "Layer Normalization"
  Jimmy Lei Ba, Jamie Ryan Kiros, Geoffrey E. Hinton
  and is applied before the internal nonlinearities.
  Recurrent dropout is base on:
    https://arxiv.org/abs/1603.05118
  "Recurrent Dropout without Memory Loss"
  Stanislau Semeniuta, Aliaksei Severyn, Erhardt Barth.
  """

  def __init__(self,
               num_units,
               forget_bias=1.0,
               activation=tf.tanh,
               layer_norm=True,
               norm_gain=1.0,
               norm_shift=0.0,
               recurrent_keep_prob=1.0,
               input_weight_keep_prob=1.0,
               recurrent_weight_keep_prob=1.0,
               dropout_seed=None,
               weight_variational=False,
               reuse=None,
               dtype=None):
    """Initializes the basic LSTM cell.
    Args:
      input_weight is W
      recurrent_weight is U
      num_units: int, The number of units in the LSTM cell.
      forget_bias: float, The bias added to forget gates (see above).
      input_size: Deprecated and unused.
      activation: Activation function of the inner states.
      layer_norm: If `True`, layer normalization will be applied.
      norm_gain: float, The layer normalization gain initial value. If
        `layer_norm` has been set to `False`, this argument will be ignored.
      norm_shift: float, The layer normalization shift initial value. If
        `layer_norm` has been set to `False`, this argument will be ignored.
      dropout_keep_prob: unit Tensor or float between 0 and 1 representing the
        recurrent dropout probability value. If float and 1.0, no dropout will
        be applied.
      dropout_prob_seed: (optional) integer, the randomness seed.
      reuse: (optional) Python boolean describing whether to reuse variables
        in an existing scope.  If not `True`, and the existing scope already has
        the given variables, an error is raised.
    """
    super(WeightDropLayerNormBasicLSTMCell, self).__init__(_reuse=reuse)

    self._num_units = num_units
    self._activation = activation
    self._forget_bias = forget_bias
    self._recurrent_keep_prob = recurrent_keep_prob
    self._input_weight_keep_prob = input_weight_keep_prob
    self._recurrent_weight_keep_prob = recurrent_weight_keep_prob
    self._dropout_seed = dropout_seed
    self._layer_norm = layer_norm
    self._norm_gain = norm_gain
    self._norm_shift = norm_shift
    self._reuse = reuse
    self._weight_variational = weight_variational
    self._dtype = dtype

    self._input_weight_noise = None
    self._recurrent_weight_noise = None


    if self._weight_variational:
      if dtype is None:
        raise ValueError(
            "When weight_variational=True, dtype must be provided")


      # def convert_to_batch_shape(s):
      #   # Prepend a 1 for the batch dimension; for recurrent
      #   # variational dropout we use the same dropout mask for all
      #   # batch elements.
      #   return tf.concat(([1], s.get_shape().as_list()), 0)

      # def batch_noise(s, seed):
      #   shape = convert_to_batch_shape(s)
      #   return random_ops.random_uniform(shape, seed=seed, dtype=dtype)

      # self._input_weight_noise = enumerated_map_structure_up_to(
      #     cell.state_size,
      #     lambda i, s: batch_noise(s, seed=self._dropout_seed),
      #     cell.state_size)
      # self._recurrent_weight_noise = _enumerated_map_structure_up_to(
      #     cell.output_size,
      #     lambda i, s: batch_noise(s, seed=self._gen_seed("output", i)),
      #     cell.output_size)

  # def _enumerated_map_structure_up_to(shallow_structure, map_fn, *args, **kwargs):
  #   ix = [0]
  #   def enumerated_fn(*inner_args, **inner_kwargs):
  #     r = map_fn(ix[0], *inner_args, **inner_kwargs)
  #     ix[0] += 1
  #     return r
  #   return nest.map_structure_up_to(shallow_structure,
  #                                   enumerated_fn, *args, **kwargs)

  @property
  def state_size(self):
    return tf.contrib.rnn.LSTMStateTuple(self._num_units, self._num_units)

  @property
  def output_size(self):
    return self._num_units

  def _norm(self, inp, scope, dtype=tf.float32):
    shape = inp.get_shape()[-1:]
    gamma_init = tf.constant_initializer(self._norm_gain)
    beta_init = tf.constant_initializer(self._norm_shift)
    with tf.variable_scope(scope): # replace vs with tf. vs stands for va
      # Initialize beta and gamma for use by layer_norm.
      tf.get_variable("gamma", shape=shape, initializer=gamma_init, dtype=dtype)
      tf.get_variable("beta", shape=shape, initializer=beta_init, dtype=dtype)
    normalized = tf.contrib.layers.layer_norm(inp, reuse=True, scope=scope)
    return normalized

  def _linear(self, args, inputs_shape, h_shape):
    out_size = 4 * self._num_units
    proj_size = args.get_shape()[-1]
    dtype = args.dtype
    weights = tf.get_variable("kernel", [proj_size, out_size], dtype=dtype)

    w, u = tf.split(weights, [inputs_shape, h_shape], axis=0)

    if self._should_drop(self._input_weight_keep_prob):
      w = self._dropout(w, self._input_weight_noise, self._input_weight_keep_prob)
    if self._should_drop(self._recurrent_weight_keep_prob):
      u = self._dropout(u, self._recurrent_weight_noise, self._recurrent_weight_keep_prob)

    weights = tf.concat([w, u], 0)

    out = tf.matmul(args, weights)
    if not self._layer_norm:
      bias = tf.get_variable("bias", [out_size], dtype=dtype)
      out = tf.nn.bias_add(out, bias)
    return out

  def _variational_dropout(self, values, noise, keep_prob):
    """Performs dropout given the pre-calculated noise tensor."""
    # uniform [keep_prob, 1.0 + keep_prob)
    # random_tensor = keep_prob + noise

    # # 0. if [keep_prob, 1.0) and 1. if [1.0, 1.0 + keep_prob)
    # binary_tensor = tf.floor(random_tensor)
    # ret = tf.div(value, keep_prob) * binary_tensor
    # ret.set_shape(value.get_shape())
    # return ret
    return tf.nn.dropout(values, keep_prob, seed=self._dropout_seed)

  def _dropout(self, values, dropout_noise, keep_prob):
    # when it gets in here, keep_prob < 1.0
    if not self._weight_variational:
      return tf.nn.dropout(values, keep_prob, seed=self._dropout_seed)
    else:
      return self._variational_dropout(values, dropout_noise, keep_prob)


  def _should_drop(self, p):
    return (not isinstance(p, float)) or p < 1

  def call(self, inputs, state):
    """LSTM cell with layer normalization and recurrent dropout."""
    c, h = state
    args = tf.concat([inputs, h], 1)
    concat = self._linear(args, inputs.get_shape().as_list()[-1], h.get_shape().as_list()[-1])
    dtype = args.dtype

    i, j, f, o = tf.split(value=concat, num_or_size_splits=4, axis=1)

    if self._layer_norm:
      i = self._norm(i, "input", dtype=dtype)
      j = self._norm(j, "transform", dtype=dtype)
      f = self._norm(f, "forget", dtype=dtype)
      o = self._norm(o, "output", dtype=dtype)

    g = self._activation(j)
    if self._should_drop(self._recurrent_keep_prob):
      g = tf.nn.dropout(g, self._recurrent_keep_prob, seed=self._dropout_seed)      

    new_c = (
        c * tf.sigmoid(f + self._forget_bias) + tf.sigmoid(i) * g)
    if self._layer_norm:
      new_c = self._norm(new_c, "state", dtype=dtype)
    new_h = self._activation(new_c) * tf.sigmoid(o)

    new_state = tf.contrib.rnn.LSTMStateTuple(new_c, new_h)
    return new_h, new_state