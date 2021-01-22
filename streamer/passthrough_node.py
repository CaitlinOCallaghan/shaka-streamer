
# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A module that pushes a pre-encoded input to ffmpeg in order to generate a live manifest."""

from streamer.input_configuration import InputConfig
from streamer.node_base import PolitelyWaitOnFinish
from streamer.output_stream import OutputStream
from streamer.pipeline_configuration import PipelineConfig, StreamingMode
from typing import List

class PassthroughNode(PolitelyWaitOnFinish):

  def __init__(self,
               input_config: InputConfig,
               pipeline_config: PipelineConfig,
               outputs: List[OutputStream]) -> None:
    super().__init__()
    self._input_config = input_config
    self._pipeline_config = pipeline_config
    self._outputs = outputs

  def start(self) -> None:
    args = [
       'ffmpeg',
        # Do not prompt for output files that already exist. Since we created
        # the named pipe in advance, it definitely already exists. A prompt
        # would block ffmpeg to wait for user input.
        '-hide_banner',
        '-y',
    ]

    if self._pipeline_config.quiet:
      args += [
          # Suppresses all messages except errors.
          # Without this, a status line will be printed by default showing
          # progress and transcoding speed.
          '-loglevel', 'error',
      ]

    if self._pipeline_config.streaming_mode == StreamingMode.LIVE:
      args += ['-re']
      
    # The input name always comes after the applicable input arguments.
    for input in self._input_config.inputs:
      args += [  
         # The input itself.
        '-i', input.get_path_for_passthrough(),
        '-c:v', 'copy',
        '-an',
        '-f', 'mpegts'
      ]

    for output_stream in self._outputs:
      # The output pipe.
      args += [output_stream.pipe]

    env = {}
    self._process = self._create_process(args, env)