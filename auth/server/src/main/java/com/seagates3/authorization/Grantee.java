/*
 * COPYRIGHT 2019 SEAGATE LLC
 *
 * THIS DRAWING/DOCUMENT, ITS SPECIFICATIONS, AND THE DATA CONTAINED
 * HEREIN, ARE THE EXCLUSIVE PROPERTY OF SEAGATE TECHNOLOGY
 * LIMITED, ISSUED IN STRICT CONFIDENCE AND SHALL NOT, WITHOUT
 * THE PRIOR WRITTEN PERMISSION OF SEAGATE TECHNOLOGY LIMITED,
 * BE REPRODUCED, COPIED, OR DISCLOSED TO A THIRD PARTY, OR
 * USED FOR ANY PURPOSE WHATSOEVER, OR STORED IN A RETRIEVAL SYSTEM
 * EXCEPT AS ALLOWED BY THE TERMS OF SEAGATE LICENSES AND AGREEMENTS.
 *
 * YOU SHOULD HAVE RECEIVED A COPY OF SEAGATE'S LICENSE ALONG WITH
 * THIS RELEASE. IF NOT PLEASE CONTACT A SEAGATE REPRESENTATIVE
 * http://www.seagate.com/contact
 *
 * Original author:  Abhilekh Mustapure <abhilekh.mustapure@seagate.com>
 * Original creation date: 04-Apr-2019
 */

/* This Class Represents Xml Node As
 *    <Grantee xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 *      xsi:type="CanonicalUser">
 *      <ID>Int</ID>
 *      <DisplayName>String</DisplayName>
 *    </Grantee>
 */

package com.seagates3.authorization;

public class Grantee {

    String canonicalId;
    String displayName;

    public Grantee (String canonicalId, String displayName) {
        this.canonicalId = canonicalId;
        this.displayName = displayName;
    }

    void setCanonicalId(String Id) {
       canonicalId = Id;
    }

    private String getCanonicalId() {
        return canonicalId;
    }

    void setDisplayName(String Name) {
        displayName = Name;
    }

    private String getDisplayName() {
        return displayName;
    }



}

