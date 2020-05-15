/* Copyright 2018 Intel Corporation
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <stdlib.h>
#include <string>

#include "error.h"
#include "tcf_error.h"
#include "swig_utils.h"
#include "types.h"

#include "work_order_singleton.h"

#include "base.h"
#include "work_order_wrap.h"

std::string HandleWorkOrderRequest(
    const std::string& serialized_request) {
    tcf_err_t presult;

    uint32_t response_identifier;
    size_t response_size;

    tcf::enclave_queue::ReadyEnclave readyEnclave = \
        tcf::enclave_api::base::GetReadyEnclave();

    WorkOrderHandlerSingleton wo_handle;
    presult = wo_handle.HandleWorkOrderRequest(
        serialized_request,
        response_identifier,
        response_size,
        readyEnclave.getIndex());
    ThrowTCFError(presult);

    Base64EncodedString response;
    presult = WorkOrderHandlerBase::GetSerializedResponse(
        response_identifier,
        response_size,
        response,
        readyEnclave.getIndex());
    ThrowTCFError(presult);
    return response;
}
